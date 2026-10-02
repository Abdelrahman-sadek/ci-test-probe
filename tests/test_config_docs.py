"""Every AGENTKIT_* setting the code reads is documented in .env.example."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_setting_is_documented():
    code = set()
    for f in [*ROOT.glob("agentkit/**/*.py"), *ROOT.glob("scripts/*.py")]:
        code |= {n for n in re.findall(r"AGENTKIT_[A-Z0-9_]+", f.read_text(encoding="utf-8"))
                 if not n.endswith("_")}  # "AGENTKIT_PRIMO_{k}" is a prefix, not a setting
    documented = set(re.findall(r"AGENTKIT_[A-Z0-9_]*[A-Z0-9]", (ROOT / ".env.example").read_text(encoding="utf-8")))
    missing = sorted(code - documented)
    assert not missing, f"add to .env.example: {missing}"
