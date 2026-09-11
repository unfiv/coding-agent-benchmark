import sys
import os
import json
import subprocess
import argparse
import urllib.request
import urllib.error
import logging

# 0. Настройка файлового логирования
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/agent_execution.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

def log(msg):
    sys.stderr.write(f"{msg}\n")
    logging.info(msg)

# 1. Определение тулов для агента
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a bash command in workspace (e.g. cmake, ctest, git diff)",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read content of a file relative to workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative file path"}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace a specific code snippet in an existing file with new code. Always use this instead of write_file for existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative file path"},
                    "old_code": {"type": "string", "description": "Exact code block to remove/replace"},
                    "new_code": {"type": "string", "description": "New code block to insert"}
                },
                "required": ["filepath", "old_code", "new_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a completely NEW file. NEVER use this on existing files, use replace_in_file instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative file path"},
                    "content": {"type": "string", "description": "New file content"}
                },
                "required": ["filepath", "content"]
            }
        }
    }
]

# 2. Исполнитель тулов
def execute_tool(name, args):
    if name == "run_bash":
        res = subprocess.run(args["command"], shell=True, capture_output=True, text=True)
        stdout = res.stdout[-1500:] if len(res.stdout) > 1500 else res.stdout
        stderr = res.stderr[-1500:] if len(res.stderr) > 1500 else res.stderr
        return f"STDOUT (tail):\n{stdout}\nSTDERR (tail):\n{stderr}\nEXIT_CODE: {res.returncode}"
    
    elif name == "read_file":
        try:
            with open(args["filepath"], "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"
            
    elif name == "replace_in_file":
        try:
            with open(args["filepath"], "r", encoding="utf-8") as f:
                content = f.read()
            if args["old_code"] not in content:
                return "Error: old_code snippet was not found in the file. Make sure you match exact spacing and line breaks."
            new_content = content.replace(args["old_code"], args["new_code"], 1)
            with open(args["filepath"], "w", encoding="utf-8") as f:
                f.write(new_content)
            return "Snippet replaced successfully."
        except Exception as e:
            return f"Error replacing in file: {str(e)}"
            
    elif name == "write_file":
        try:
            os.makedirs(os.path.dirname(args["filepath"]), exist_ok=True)
            with open(args["filepath"], "w", encoding="utf-8") as f:
                f.write(args["content"])
            return "File created/written successfully."
        except Exception as e:
            return f"Error writing file: {str(e)}"
            
    return "Unknown tool."

# 3. HTTP-запрос к OpenRouter
def call_openrouter(messages, model, api_key):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/coding-agent-benchmark",
        "X-Title": "Coding Agent Benchmark"
    }
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto"
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        log(f"OpenRouter HTTP Error: {e.code} {error_body}")
        raise RuntimeError(f"OpenRouter API HTTP {e.code}: {error_body}")
    except Exception as e:
        log(f"OpenRouter Connection Error: {str(e)}")
        raise RuntimeError(f"OpenRouter Connection Error: {str(e)}")

# 4. Основной ReAct-цикл
def main():
    # если вдруг по причине ручного сброса процесса репозиторий остался грязным (сохраняем директорию с билдом, чтобы не пересобирать каждый раз)
    subprocess.run("git -C workspace/googletest checkout . && git -C workspace/googletest clean -fd -e build", shell=True, capture_output=True)
    
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost = 0.0

    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--model", default="openai/gpt-4o")
        parser.add_argument("--max-steps", type=int, default=10)

        args, unknown_args = parser.parse_known_args()

        prompt_text = ""
        if not sys.stdin.isatty():
            raw_stdin = sys.stdin.read().strip()
            if raw_stdin:
                try:
                    data = json.loads(raw_stdin)
                    prompt_text = data.get("prompt", raw_stdin)
                except json.JSONDecodeError:
                    prompt_text = raw_stdin

        if not prompt_text and unknown_args:
            prompt_text = " ".join(unknown_args).strip()

        if not prompt_text:
            prompt_text = "Fix the task"

        api_key = os.environ.get("OPENROUTER_API_KEY", "")

        log(f"=== Starting Agent Run (max_steps={args.max_steps}, model={args.model}) ===")

        messages = [
            {
                "role": "system", 
                "content": (
                    "You are an expert C++ developer working in ./workspace/googletest.\n"
                    "CRITICAL: Never attempt to rewrite existing source/header files using write_file. "
                    "Always read the file first and use replace_in_file to modify exact code snippets."
                )
            },
            {"role": "user", "content": prompt_text}
        ]

        for step in range(args.max_steps):
            log(f"--- ReAct Step {step + 1}/{args.max_steps} ---")
            res = call_openrouter(messages, args.model, api_key)

            usage = res.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            if "cost" in res:
                total_cost += res["cost"]
            elif "cost" in usage:
                total_cost += usage["cost"]

            choice = res["choices"][0]
            msg = choice["message"]
            messages.append(msg)

            if not msg.get("tool_calls"):
                log("Agent finished task without further tool calls.")
                break

            for tool_call in msg["tool_calls"]:
                fn_name = tool_call["function"]["name"]
                fn_args = json.loads(tool_call["function"]["arguments"])
                log(f"Executing tool: {fn_name} with args: {json.dumps(fn_args)}")
                
                tool_output = execute_tool(fn_name, fn_args)
                log(f"Tool [{fn_name}] Output Tail:\n{tool_output[-300:]}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": tool_output
                })

        final_text = ""
        for msg in reversed(messages):
            content = msg.get("content")
            if content and isinstance(content, str):
                final_text = content
                break

        if not final_text:
            final_text = "Task finished without a final text message."

    except Exception as e:
        log(f"Agent failed with exception: {str(e)}")
        final_text = f"Agent failed with error: {str(e)}"

    output_data = {
        "output": final_text,
        "tokenUsage": {
            "prompt": total_prompt_tokens,
            "completion": total_completion_tokens,
            "total": total_prompt_tokens + total_completion_tokens
        },
        "cost": round(total_cost, 6)
    }

    log(f"=== Execution Finished. Total Cost: ${total_cost:.6f} ===")
    print(json.dumps(output_data))
    sys.exit(0)

if __name__ == "__main__":
    main()