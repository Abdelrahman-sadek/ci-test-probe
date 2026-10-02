#!/usr/bin/env bash
# Validates every agent file against docs/AGENT-DESIGN.md.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0; count=0
for f in agents/*/*.md; do
  count=$((count+1)); base=$(basename "$f" .md)
  err() { echo "✗ $f: $1"; fail=1; }
  [ "$(head -1 "$f")" = "---" ] || err "missing frontmatter"
  fm=$(awk 'NR==1{next} /^---$/{exit} {print}' "$f")
  name=$(sed -n 's/^name:[[:space:]]*//p' <<<"$fm")
  desc=$(sed -n 's/^description:[[:space:]]*//p' <<<"$fm")
  [ "$name" = "$base" ] || err "name '$name' must equal filename '$base'"
  [[ "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || err "name must be kebab-case"
  [[ "$desc" == "Use "* ]] || err "description must start with 'Use '"
  for s in "## Rules" "## Workflow" "## Output"; do grep -q "^$s" "$f" || err "missing section '$s'"; done
  lines=$(wc -l <"$f"); [ "$lines" -le 120 ] || err "$lines lines (max 120)"
done
dupes=$(for f in agents/*/*.md; do basename "$f"; done | sort | uniq -d)
[ -z "$dupes" ] || { echo "✗ duplicate agent names: $dupes"; fail=1; }
[ $fail -eq 0 ] && echo "✓ $count agents passed" || exit 1
