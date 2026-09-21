# Coding Agent Benchmark

A test stand that measures what it actually costs an AI coding agent to solve a real task: money, tokens, turns and time, end to end.

## Why

Optimizing the cost of working with AI is hard to measure. There are many variables, many unknowns and many hidden components, so the space is full of speculation.

This project started with a token-compression proxy that looked like it was working: after a day of use the quality of solutions had not dropped, so the savings seemed justified. Local statistics showed that, because of the subscription plan in use, not a single request had been compressed. A feeling that "it works" is not a measurement.

The stand answers one question: **how much does it cost to solve this task with this tool?** It measures the whole system (model, harness, tools) as a black box, not just the model.

## Key features

* **Zero-config first run.** The smoke benchmark needs no API keys and no `.env`: clone, run one command, get `Results: 1 passed (100%)`.
* **Whole-system measurement.** The agent runs with its own harness and its full toolset. The stand measures what that system costs, not a hand-picked slice of it.
* **Reproducible environment.** The agent benchmarks pin the Docker base image by digest and Promptfoo and the Claude Code CLI to exact versions; the target repository is pinned to a commit.
* **Isolated benchmarks.** Every benchmark has its own `Dockerfile`, `compose.yaml` and checks. Only that benchmark's directory is mounted into the container, never the repository root or your `.env` file.
* **Model comparison out of the box.** Several providers in one `promptfooconfig.yaml` run as a matrix, with repeats.
* **Hard limits.** Max turns, budget in USD and a timeout per run. A run that hits a limit is classified (`STEP_LIMIT`, `BUDGET_EXCEEDED`) instead of being lost.
* **Full evidence.** Every agent event is stored in a transcript, per-run metrics go to `metrics.jsonl`, and a summary table is written at the end.

## Sample results

Task: fix an integer-overflow bug in `EXPECT_EQ` comparisons in [googletest](https://github.com/google/googletest). Agent: Claude Code with its own harness. Limits: 80 turns, $2. One run per model.

| | Opus 5 | Sonnet 5 |
| :--- | ---: | ---: |
| Solved | yes | yes |
| Cost | $1.47 | $1.30 |
| Tokens (incl. cache) | 852K | 3.36M |
| Model replies | 24 | 48 |
| API time | 4.1 min | 4.9 min |
| Wall time | 22 min | 22 min |

Sonnet was 11% cheaper but used four times the tokens and twice the replies: price per token and price per solved task are different things. One run is not a conclusion; it shows what the stand reports.

Smoke-level task (create a file), 10 repeats per model: both solved 10 of 10, at $0.0077 (Haiku 4.5) and $0.0165 (Sonnet 5) per solved task.

Raw data: [`results/cpp-gtest-fix/opus_vs_sonnet_repeat_1`](results/cpp-gtest-fix/opus_vs_sonnet_repeat_1), [`results/create-file/sonnet_vs_haiku_repeat_10`](results/create-file/sonnet_vs_haiku_repeat_10).

## What is measured

For every run:

* whether the task was solved, as decided by the benchmark's own check;
* cost in USD, prompt and completion tokens (cache reads and writes included);
* number of agent turns;
* API time and wall time;
* how the run ended: success, turn limit, budget limit, timeout.

## How it works

```
promptfoo  ->  provider (run_claude_code.py)  ->  Claude Code CLI  ->  verify.py
                          |                                               |
                          +---- transcript, usage, cost <-----------------+
                                          |
                          logs/metrics.jsonl -> logs/summary.md, summary.json
```

* **Orchestration.** [Promptfoo](https://www.promptfoo.dev/) runs the matrix of models and repeats.
* **Thin launcher.** The provider starts the agent, enforces the limits and records everything. It does not restrict the agent: the harness is part of what is being measured.
* **Success criterion.** Each benchmark defines its own check in `verify.py`. For the googletest task it rebuilds the library, runs an additional regression test for the bug, then runs the repository's `ctest` suite.

### Inside the container

* **Image.** Node.js 22 (pinned by digest) with Promptfoo `0.123.0` and the Claude Code CLI `2.1.269`. The C++ benchmark adds `build-essential`, CMake and Ninja.
* **User.** The agent runs as the unprivileged `node` user. The stand starts it with `--dangerously-skip-permissions` so it can work unattended, and Claude Code refuses that flag under root.
* **Mounts.** The benchmark's own directory is bind-mounted read-write (`/app` in `cpp-gtest-fix`, `/work` in `create-file`). API keys reach the container as environment variables; the `.env` file itself is not mounted.
* **Target repository.** googletest is cloned into `workspace/` at a pinned commit on the first run and reset to a clean state before each following run.
* **Output.** Logs, transcripts and the Promptfoo database are written to `logs/` on the host, so they survive `--rm`.
* **Network.** Not restricted: the agent can reach the Internet, as it can with its normal harness.

## Quick start

### 1. Smoke test (no keys)

Validates Docker, Promptfoo and the pipeline with a local loopback evaluation:

```bash
docker compose -f benchmarks/smoke/compose.yaml run --rm benchmark promptfoo eval
```

Expected: `Results: 1 passed (100%)`.

### 2. `create-file` (real agent, cents)

The agent creates a single file and the check reads it back. Two models, 10 repeats each: about $0.25 and a couple of minutes in total.

```bash
cp .env.example .env    # then put your ANTHROPIC_API_KEY into .env
docker compose -f benchmarks/create-file/compose.yaml run --rm benchmark
```

The repository already contains `.env.example`. Compose stops with an error if `.env` is missing. Use a dedicated key with a spend limit: the agent runs unattended and can read its environment.

### 3. `cpp-gtest-fix` (real task, dollars)

Same `.env`. About $1.3-1.5 and 20+ minutes per model. The first run also clones and builds googletest, so it needs network access.

```bash
docker compose -f benchmarks/cpp-gtest-fix/compose.yaml run --rm benchmark
```

Do not append `promptfoo eval` to the commands of benchmarks 2 and 3: their `compose.yaml` already defines the whole command (setup, eval, report), and an extra argument would replace it.

## Configuration

* **`.env`**: `ANTHROPIC_API_KEY`, required by the two agent benchmarks.
* **Model and limits** are set per provider in each `promptfooconfig.yaml`: `model`, `maxTurns`, `maxBudgetUsd`, plus `repeat`, `timeoutMs` and `maxConcurrency` under `evaluateOptions`. Add another entry under `providers:` to add a model to the comparison.
* **Environment fallbacks.** `CLAUDE_MODEL`, `CLAUDE_MAX_TURNS` and `CLAUDE_MAX_BUDGET_USD` are used only when a provider omits the value (for example, when running `run_claude_code.py` by hand). They do not override `promptfooconfig.yaml`.
* **`MCP_CONFIG_PATH`** (optional): a path to an MCP config, passed to the agent as `--mcp-config`.

## Benchmarks

| Benchmark | What it does | Cost per run |
| :--- | :--- | :--- |
| `smoke` | Checks that Docker and Promptfoo work; no agent | free |
| `create-file` | Agent creates `workspace/hello.txt`; Sonnet 5 vs Haiku 4.5, 10 repeats | about $0.25 in total |
| `cpp-gtest-fix` | Agent fixes an integer-overflow bug in googletest; Sonnet 5 vs Opus 5 | about $1.3-1.5 per model, about 22 min |

## Output

Every run writes to `benchmarks/<name>/logs/` (git-ignored):

| File | Content |
| :--- | :--- |
| `summary.md`, `summary.json` | aggregated table per model |
| `metrics.jsonl` | one line per run |
| `transcripts/<run_id>.jsonl` | every agent event, verbatim |
| `claude_code_execution.log` | human-readable digest |
| `results.json` | Promptfoo's structured result |

Published examples live in `results/<benchmark>/<experiment>/`.

## Cleanup

`--rm` already removes the container after each run. To remove the built image:

```bash
docker compose -f benchmarks/<name>/compose.yaml down --rmi all --remove-orphans
```

## Adding a benchmark

1. Create `benchmarks/<name>/`.
2. Add a `Dockerfile` with the toolchain, and pin versions.
3. Add a `compose.yaml` with a unique image and container name. Mount only the benchmark directory and pass the keys via `env_file`.
4. Copy `run_claude_code.py` and `bench_report.py` from an existing benchmark (they are identical today).
5. Describe providers and limits in `promptfooconfig.yaml`, and the success check in `verify.py` (a `get_assert(output, context)` function).

## Limitations

* **The agent side is not fully controlled.** The stand runs the agent's own harness. A tool whitelist can be configured, but a new agent feature can change behavior at any time, and reproducibility of results drifts with it.
* **Behavior is part of the result.** In one run Sonnet 5 decided to research the problem on the Internet instead of fixing it with language mechanisms, ran out of agent turns and failed the test case. That is a measurement too: the stand answers "what does it cost to solve the task", not "what does the model do in an ideal setting".
* **The agent has broad rights inside the container:** permissions are skipped, the network is open, and API keys are visible in its environment. Use a dedicated key with a spend limit.
* **Small samples.** Expensive tasks have one run per model; treat them as examples, not conclusions.
* **Wall time includes waiting** for builds and tests, not only model time; see API time.

## Roadmap

* A reference task between `create-file` and `cpp-gtest-fix`: cheap enough to run dozens of times, meaningful enough to check something.
* Repeats and spread in the summary.
* Comparing tool setups on the same model, for example IDE-indexed search versus grep, via `MCP_CONFIG_PATH`.
* More agents and providers.

## License

Apache License 2.0, see [LICENSE](LICENSE). Benchmark targets keep their own licenses: googletest is fetched at run time and is not part of this repository.