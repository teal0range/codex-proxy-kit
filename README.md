# codex-proxy-kit

`codex-proxy-kit` is a small toolkit for two common `Codex CLI` debugging/integration problems:

- running Codex against a non-standard OpenAI-compatible backend such as `vLLM`
- logging raw `/v1/responses` traffic and SSE streams from an official OpenAI-backed Codex session

The repo contains two independent tools plus lightweight wrappers:

- [`scripts/codex_vllm_responses_proxy.py`](/mnt/e/code/codex-proxy-kit/scripts/codex_vllm_responses_proxy.py)
  - compatibility proxy for `Codex CLI -> /v1/responses -> vLLM-compatible backend`
- [`scripts/codex_openai_log_proxy.py`](/mnt/e/code/codex-proxy-kit/scripts/codex_openai_log_proxy.py)
  - transparent local logging proxy for official OpenAI traffic
- [`scripts/codex-vllm`](/mnt/e/code/codex-proxy-kit/scripts/codex-vllm)
  - convenience wrapper for the vLLM compatibility proxy
- [`scripts/codex-openai-log`](/mnt/e/code/codex-proxy-kit/scripts/codex-openai-log)
  - convenience wrapper for the OpenAI logging proxy

## Supported Versions

Tested directly with:

- `codex-cli 0.116.0`

Validated assumptions for this version:

- Codex uses the `Responses API`
- tool execution is driven by `function_call` / `function_call_output`
- prefixed tool names such as `functions.exec_command` are rejected by the local dispatcher
- Codex expects streamed `/v1/responses` traffic that can be represented as SSE events such as `response.output_item.added`

Older or newer versions may still work, but the vLLM compatibility proxy is intentionally tuned to the behavior of `codex-cli 0.116.0`.

## Features

### `codex-vllm`

- rewrites alias model ids such as `gpt-5.4` to a target backend model
- patches `/v1/models` so Codex can see a friendly alias model id
- normalizes multi-turn `input` items for `Responses API`
- converts tool names like `functions.exec_command` to bare names like `exec_command`
- rewrites tool-markup text into structured `function_call` items
- can synthesize minimal SSE for streamed `/v1/responses`

### `codex-openai-log`

- logs request bodies sent to `/v1/responses`
- logs raw JSON responses and raw SSE event streams
- keeps Codex pointing at a local plain HTTP proxy while forwarding upstream to `https://api.openai.com`
- useful for debugging `web_search`, tool calls, and stream event ordering

## Requirements

- Linux or WSL
- Python `3.10+`
- `codex` already installed
- `codex` already authenticated
- `requests` available for the OpenAI logging proxy

## Install

### One-line install

From inside the repo:

```bash
./install.sh
```

That installs wrapper commands into `~/.local/bin`:

- `codex-vllm`
- `codex-openai-log`

If `~/.local/bin` is not already on your `PATH`, add:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Manual install

```bash
python3 -m pip install -r requirements.txt
chmod +x scripts/codex-vllm scripts/codex-openai-log \
  scripts/codex_vllm_responses_proxy.py scripts/codex_openai_log_proxy.py
```

## Quick Start

### 1. Run Codex against a vLLM backend

Start via wrapper:

```bash
codex-vllm \
  --provider myvllm \
  --alias-model gpt-5.4 \
  --target-model kimi-k2.5 \
  --upstream http://127.0.0.1:8000 \
  exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox
```

Wrapper behavior:

- starts the compatibility proxy on `127.0.0.1:18001` if needed
- points Codex at that proxy with a temporary custom provider override
- keeps the visible model id as `gpt-5.4` by default while routing to the target backend model

Default environment variables:

- `CODEX_VLLM_PROVIDER=localvllm`
- `CODEX_VLLM_ALIAS_MODEL=gpt-5.4`
- `CODEX_VLLM_TARGET_MODEL=kimi-k2.5`
- `CODEX_VLLM_UPSTREAM=http://127.0.0.1:8000`
- `CODEX_VLLM_LISTEN_PORT=18001`
- `CODEX_VLLM_LOG_DIR=~/.local/share/codex-vllm-proxy/logs`

### 2. Log official OpenAI Codex traffic locally

Run:

```bash
codex-openai-log exec --skip-git-repo-check --json "search the web for mimo"
```

Wrapper behavior:

- starts the logging proxy on `127.0.0.1:18021` if needed
- points Codex at `http://127.0.0.1:18021/v1`
- forwards upstream to `https://api.openai.com`

Logs are written to:

- `~/.local/share/codex-openai-log-proxy/logs`

Each request produces:

- `*.request.json`
- `*.response.log`

## Repository Layout

```text
codex-proxy-kit/
├── install.sh
├── requirements.txt
├── scripts/
│   ├── codex-vllm
│   ├── codex-openai-log
│   ├── codex_vllm_responses_proxy.py
│   └── codex_openai_log_proxy.py
└── README.md
```

## Troubleshooting

### Codex says a tool call is unsupported

Inspect:

```bash
find ~/.codex/sessions -type f | tail
```

Then look for `function_call` and `function_call_output` items inside the latest `jsonl`.

### vLLM returns raw tool-call markup instead of structured tool calls

That is exactly what `codex_vllm_responses_proxy.py` is designed to normalize. Check:

- `~/.local/share/codex-vllm-proxy/logs/proxy.log`

### Local web search behavior is unclear

Use `codex-openai-log` and inspect the generated `*.request.json` and `*.response.log` files.

## Verification

```bash
python3 -m py_compile scripts/codex_vllm_responses_proxy.py
python3 -m py_compile scripts/codex_openai_log_proxy.py
scripts/codex-openai-log --version
```

## License

MIT
