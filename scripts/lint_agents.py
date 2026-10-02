"""Validate agents, skills, the plugin marketplace and smoke-test coverage. Exit 1 on any error."""
import json
import re
import sys
from pathlib import Path

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MODELS = {"haiku", "sonnet", "opus", "fable", "inherit"}
COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
MAX_LINES, MAX_DESC = 120, 300
# antislop (github.com/miqdadbadjuber/anti-slop): words that make agent instructions vague and their output generic.
SLOP = re.compile(r"\b(delve|seamless(ly)?|robust|elevate|unlock|empower|cutting[- ]edge|game[- ]changer|"
                  r"next[- ]level|world[- ]class|best[- ]in[- ]class|synerg\w*|revolutioni[sz]\w*|"
                  r"i hope this helps|feel free to)\b", re.I)


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---" not in text[4:]:
        return {}, text
    fm, body = text[4:].split("\n---", 1)
    meta = {}
    for line in fm.splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.split("  #")[0].strip()
    return meta, body


def lint_agent(path: Path) -> list[str]:
    errs, meta, body = [], *frontmatter(path)
    if not meta:
        return [f"{path}: missing frontmatter"]
    name, desc = meta.get("name", ""), meta.get("description", "")
    if name != path.stem:
        errs.append(f"{path}: name '{name}' must equal filename '{path.stem}'")
    if not KEBAB.match(name):
        errs.append(f"{path}: name must be kebab-case")
    if not desc.startswith("Use "):
        errs.append(f"{path}: description must start with 'Use '")
    if len(desc) > MAX_DESC:
        errs.append(f"{path}: description is {len(desc)} chars (max {MAX_DESC}) — move detail into the body")
    model = meta.get("model", "inherit")
    if model not in MODELS and not model.startswith("claude-"):
        errs.append(f"{path}: model '{model}' not one of {sorted(MODELS)} or a claude-* ID")
    if "color" in meta and meta["color"] not in COLORS:
        errs.append(f"{path}: color '{meta['color']}' not one of {sorted(COLORS)}")
    if "maxTurns" in meta and not meta["maxTurns"].isdigit():
        errs.append(f"{path}: maxTurns must be a positive integer")
    for section in ("## Rules", "## Workflow", "## Output"):
        if not re.search(rf"^{re.escape(section)}", body, re.M):
            errs.append(f"{path}: missing section '{section}'")
    for m in SLOP.finditer(body):
        errs.append(f"{path}: filler word '{m.group(0)}' — say what the agent does instead")
    lines = path.read_text(encoding="utf-8").count("\n") + 1
    if lines > MAX_LINES:
        errs.append(f"{path}: {lines} lines (max {MAX_LINES})")
    return errs


def lint_skill(path: Path) -> list[str]:
    meta, _ = frontmatter(path)
    name, desc = meta.get("name", ""), meta.get("description", "")
    errs = []
    if name != path.parent.name or not KEBAB.match(name) or len(name) > 64:
        errs.append(f"{path}: skill name '{name}' must be kebab-case, ≤ 64 chars and equal its folder name")
    if not desc or len(desc) > 1024:
        errs.append(f"{path}: skill description required, ≤ 1024 chars")
    return errs


def lint(root: Path) -> tuple[list[str], int]:
    agents = sorted(root.glob("plugins/*/agents/*.md"))
    errs = [e for a in agents for e in lint_agent(a)]
    errs += [e for s in sorted(root.glob("plugins/*/skills/*/SKILL.md")) for e in lint_skill(s)]
    names = [a.stem for a in agents]
    errs += [f"duplicate agent name: {n}" for n in sorted({n for n in names if names.count(n) > 1})]
    mk_path = root / ".claude-plugin/marketplace.json"
    if mk_path.exists():
        try:
            mk = json.loads(mk_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return errs + [f"{mk_path}: invalid JSON ({e})"], len(agents)
        for key in ("name", "owner", "plugins"):
            if key not in mk:
                errs.append(f"{mk_path}: missing '{key}'")
        listed = set()
        for entry in mk.get("plugins", []):
            src = entry.get("source", "")
            if not KEBAB.match(entry.get("name", "")):
                errs.append(f"{mk_path}: plugin name '{entry.get('name')}' must be kebab-case")
            if not src.startswith("./") or ".." in src or not (root / src).is_dir():
                errs.append(f"{mk_path}: plugin '{entry.get('name')}' source '{src}' must be an existing ./ path")
            listed.add((root / src).resolve())
        for d in sorted({a.parent.parent.resolve() for a in agents} - listed):
            errs.append(f"{mk_path}: plugin folder {d.relative_to(root.resolve())} is not listed in the marketplace")
    smoke = root / "evals/agents/smoke.json"
    if smoke.exists():
        missing = sorted(set(names) - set(json.loads(smoke.read_text(encoding="utf-8"))))
        errs += [f"{smoke}: no smoke test for agent '{n}'" for n in missing]
    return errs, len(agents)


if __name__ == "__main__":
    errors, count = lint(Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent))
    for e in errors:
        print(f"✗ {e}")
    print(f"✓ {count} agents passed" if not errors else f"{len(errors)} problem(s) in {count} agents")
    sys.exit(1 if errors else 0)
