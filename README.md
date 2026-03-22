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
  - config-driven wrapper for the vLLM compatibility proxy
- [`scripts/codex-switch`](/mnt/e/code/codex-proxy-kit/scripts/codex-switch)
  - alias for `codex-vllm`, intended for interactive multi-model use
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

- reads a JSON model-routing config
- exposes all configured models through one `/v1/models` endpoint
- lets Codex switch models from the interactive `/model` menu
- routes each request by `body.model` to the matching upstream API
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
- `codex-switch`
- `codex-openai-log`

If `~/.local/bin` is not already on your `PATH`, add:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Manual install

```bash
python3 -m pip install -r requirements.txt
chmod +x scripts/codex-vllm scripts/codex-switch scripts/codex-openai-log \
  scripts/codex_vllm_responses_proxy.py scripts/codex_openai_log_proxy.py
```

## Quick Start

### 1. Configure multiple upstream models

The repo ships with a ready-to-use `login002` profile and uses it by default.

If you want your own variant, copy it and edit:

```bash
cp profiles/login002.json ~/.config/codex-proxy-kit/login002.json
```

Example profile:

```json
{
  "default_model": "gpt-5.4",
  "models": [
    {
      "name": "gpt-5.4",
      "target_model": "kimi-k2.5",
      "upstream_base": "http://gpuh201:8000",
      "context_window": 262144,
      "aliases": ["kimi-k2.5"]
    },
    {
      "name": "gpt-5.2",
      "target_model": "glm-5",
      "upstream_base": "http://gpuh202:8000",
      "context_window": 131072,
      "aliases": ["glm-5"]
    },
    {
      "name": "gpt-5.1-codex-max",
      "target_model": "deepseek-v3.2",
      "upstream_base": "http://gpuh203:8000",
      "context_window": 163840,
      "aliases": ["deepseek-v3.2"]
    },
    {
      "name": "gpt-5.1-codex-mini",
      "target_model": "minimax-m2.5",
      "upstream_base": "http://gpuh204:8000",
      "context_window": 196608,
      "aliases": ["minimax-m2.5"]
    }
  ]
}
```

For `codex-cli 0.116.0`, this mapping is intentional:

- `gpt-5.4` -> `kimi-k2.5`
- `gpt-5.2` -> `glm-5`
- `gpt-5.1-codex-max` -> `deepseek-v3.2`
- `gpt-5.1-codex-mini` -> `minimax-m2.5`

This is the most reliable way to make the built-in `/model` menu switch real non-OpenAI backends without patching Codex itself.

Each model entry controls:

- the model name shown inside Codex `/model`
- the actual upstream `target_model`
- the upstream API base URL
- optional metadata like `context_window`

### 2. Run Codex against the aggregated proxy

Start via wrapper:

```bash
codex-switch \
  --config ./profiles/login002.json \
  exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox
```

Wrapper behavior:

- starts the compatibility proxy on `127.0.0.1:18011` if needed
- points Codex at that proxy with a temporary custom provider override
- starts on the profile's `default_model`
- exposes every configured model to Codex so you can switch later with `/model`

Default environment variables:

- `CODEX_VLLM_PROVIDER=localvllm`
- `CODEX_VLLM_MODELS_CONFIG=.../login002.json`
- `CODEX_VLLM_INITIAL_MODEL=gpt-5.4`
- `CODEX_VLLM_LISTEN_PORT=18011`
- `CODEX_VLLM_LOG_DIR=~/.local/share/codex-vllm-proxy/logs`

Inside Codex, switch models interactively with:

```text
/model
```

or directly:

```text
/model gpt-5.1-codex-max
```

### 3. Log official OpenAI Codex traffic locally

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
├── profiles/
│   └── login002.example.json
├── requirements.txt
├── scripts/
│   ├── codex-vllm
│   ├── codex-switch
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

### `/model` does not show your expected model list

Check the config file passed with `--config` or `CODEX_VLLM_MODELS_CONFIG`. The proxy only exposes models defined there.

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
