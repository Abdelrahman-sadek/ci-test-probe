"""Load agent Markdown files (frontmatter + body) from agents/."""
from dataclasses import dataclass
from pathlib import Path

from . import ROOT


@dataclass
class Agent:
    name: str
    description: str
    model: str
    division: str
    body: str
    path: Path


def parse(path: Path) -> Agent:
    text = path.read_text(encoding="utf-8")
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        key, _, val = line.partition(":")
        meta[key.strip()] = val.split("  #")[0].strip()
    return Agent(meta["name"], meta.get("description", ""), meta.get("model", "inherit"),
                 path.parent.name, body.strip(), path)


def load_all(root: Path = ROOT / "agents") -> dict[str, Agent]:
    return {a.name: a for a in map(parse, sorted(root.glob("*/*.md")))}
