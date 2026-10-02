#!/usr/bin/env bash
# Install agents into an AI tool.
#   scripts/install.sh claude [--project] [division...]   → ~/.claude/agents (or ./.claude/agents)
#   scripts/install.sh cursor [division...]               → ./.cursor/rules/*.mdc
#   scripts/install.sh agents-md [division...]            → ./AGENTS.generated.md (Codex, Copilot, Gemini CLI, Aider…)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tool="${1:-}"; shift || true
project=0; [ "${1:-}" = "--project" ] && { project=1; shift; }
divs=("$@"); [ ${#divs[@]} -eq 0 ] && divs=($(ls "$ROOT/agents"))
files=(); for d in "${divs[@]}"; do files+=("$ROOT"/agents/"$d"/*.md); done
body() { awk 'NR==1{next} f{print} /^---$/{f=1}' "$1"; }
field() { sed -n "s/^$2:[[:space:]]*//p" "$1" | head -1; }
case "$tool" in
  claude)
    dest="$HOME/.claude/agents"; [ $project -eq 1 ] && dest="$PWD/.claude/agents"
    mkdir -p "$dest"; cp "${files[@]}" "$dest/"; echo "Installed ${#files[@]} agents → $dest" ;;
  cursor)
    dest="$PWD/.cursor/rules"; mkdir -p "$dest"
    for f in "${files[@]}"; do
      { printf -- '---\ndescription: %s\nalwaysApply: false\n---\n' "$(field "$f" description)"; body "$f"; } >"$dest/$(basename "$f" .md).mdc"
    done; echo "Installed ${#files[@]} rules → $dest" ;;
  agents-md)
    out="$PWD/AGENTS.generated.md"
    { echo "# Agent Roster"; echo; echo "Adopt the agent whose description matches the request."; echo
      for f in "${files[@]}"; do echo "- **$(field "$f" name)** — $(field "$f" description)"; done
      for f in "${files[@]}"; do echo; echo "---"; body "$f"; done; } >"$out"
    echo "Wrote $out" ;;
  *) sed -n '2,6p' "$0"; exit 1 ;;
esac
