#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_BIN_DIR="${HOME}/.local/bin"

mkdir -p "$TARGET_BIN_DIR"

python3 -m pip install -r "${SCRIPT_DIR}/requirements.txt"

chmod +x \
  "${SCRIPT_DIR}/scripts/codex-vllm" \
  "${SCRIPT_DIR}/scripts/codex-openai-log" \
  "${SCRIPT_DIR}/scripts/codex_vllm_responses_proxy.py" \
  "${SCRIPT_DIR}/scripts/codex_openai_log_proxy.py"

ln -snf "${SCRIPT_DIR}/scripts/codex-vllm" "${TARGET_BIN_DIR}/codex-vllm"
ln -snf "${SCRIPT_DIR}/scripts/codex-openai-log" "${TARGET_BIN_DIR}/codex-openai-log"

cat <<'EOF'
Installed:
  ~/.local/bin/codex-vllm
  ~/.local/bin/codex-openai-log

If ~/.local/bin is not on PATH, add:
  export PATH="$HOME/.local/bin:$PATH"
EOF
