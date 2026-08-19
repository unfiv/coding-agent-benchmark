# Coding Agent Benchmark Stand

A modular, isolated testbed for evaluating AI coding agents on real-world engineering tasks. Designed to benchmark token cost, latency, resolution accuracy, and the efficiency of **MCP (Model Context Protocol)** integrations vs. standard LLM workflows.

---

## Key Features

* **Zero-Config First Run:** Clone and execute out-of-the-box using local mock providers without requiring third-party API keys.
* **Fully Isolated Environments:** Every benchmark module maintains its own container specification (`Dockerfile`), orchestration rules (`compose.yaml`), and dependencies.
* **Graceful Key Handling:** Centralized `.env` resolution with optional fallbacks (`required: false`) ensures tests run or fail predictably without crashing Docker infrastructure.
* **Offline & Network Fallbacks:** Real-world C++/Python targets attempt to fetch the latest repositories via GitHub, gracefully falling back to pre-packaged local codebases when offline.

---

## Quick Start (Smoke Test)

Validate your Docker infrastructure and pipeline with a 1-second local loopback evaluation:

docker compose -f benchmarks/smoke/compose.yaml run --rm benchmark promptfoo eval

**Expected Output:** `Results: 1 passed (100%)`

---

## Project Structure

coding-agent-benchmark/
├── README.md                   # Main system documentation
├── .env.example                # Shared API key template
├── .gitignore
│
├── targets/                    # Local offline copies of codebases (fallbacks)
│   └── cpp-fmt/                # Pre-packaged {fmt} library source
│
└── benchmarks/                 # Isolated benchmark suites
    ├── smoke/                  # Infrastructure smoke test (Node 22 runtime)
    │   ├── Dockerfile
    │   ├── compose.yaml
    │   └── promptfooconfig.yaml
    │
    └── cpp-fmt-fix/            # C++ task benchmark (GCC, Clang, CMake, Ninja)
        ├── Dockerfile
        ├── compose.yaml
        ├── promptfooconfig.yaml
        └── setup.sh            # Target initialization (Git fetch -> Fallback)

---

## Configuring API Keys (Optional)

Running tests against live cloud models (Anthropic Claude, OpenAI GPT-4o, etc.) requires API credentials:

1. Copy the shared environment template to the root folder:
   cp .env.example .env

2. Populate `.env` with your actual API keys:
   ANTHROPIC_API_KEY=sk-ant-api03-...
   OPENAI_API_KEY=sk-proj-...

*Note: All benchmark containers automatically mount the root `.env` file if present.*

---

## Available Benchmarks

| Benchmark Suite | Objective | Target Runtime | Run Command |
| :--- | :--- | :--- | :--- |
| **smoke** | Verify local Promptfoo testbed execution. | Node.js 22 | `docker compose -f benchmarks/smoke/compose.yaml run --rm benchmark promptfoo eval` |
| **cpp-fmt-fix** | Solve C++ type formatting bugs in `{fmt}` using IDE MCP indexing vs. raw CLI tools. | GCC 13, CMake, CTest | `docker compose -f benchmarks/cpp-fmt-fix/compose.yaml run --rm benchmark promptfoo eval` |

---

## Creating a New Benchmark Module

To add a new evaluation module:

1. Create a subdirectory under `benchmarks/<your-benchmark-name>/`.
2. Supply a dedicated `Dockerfile` containing the required build chain (e.g., Python, Rust, Go, or C++ toolchains).
3. Create a `compose.yaml` referencing `../../.env` via `required: false`.
4. Define your evaluation criteria and provider configurations inside `promptfooconfig.yaml`.