#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
prompt="$skill_dir/references/comment-sicko.md"

mkdir -p "$HOME/.claude/agents"
ln -sfn "$prompt" "$HOME/.claude/agents/comment-sicko.md"

field() { awk -v k="$1" 'NR==1 && /^---$/ {f=1; next} f && /^---$/ {exit} f && index($0, k ": ") == 1 {print substr($0, length(k) + 3)}' "$prompt"; }
body="$(awk 'NR==1 && /^---$/ {f=1; next} f && /^---$/ {f=0; next} !f' "$prompt")"

mkdir -p "$HOME/.codex/agents"
cat > "$HOME/.codex/agents/comment-sicko.toml" <<EOF
name = "$(field name)"
description = "$(field description)"
sandbox_mode = "workspace-write"
developer_instructions = '''
$body
'''
EOF

echo "claude: $HOME/.claude/agents/comment-sicko.md -> $prompt"
echo "codex:  $HOME/.codex/agents/comment-sicko.toml"
