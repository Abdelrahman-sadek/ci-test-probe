"""Review round 2: pilot gate, proxy identity, unverified scans, audience-aware conflicts, safe style edits."""
import json

import pytest

from agentkit import preflight, style
from agentkit.chat import LibraryChat, resolve_conflicts
from agentkit.llm import FakeLLM
from agentkit.rag import Chunk, Index
from agentkit.security import authenticate
from test_features import ADMIN, app  # noqa: F401  (fixture reuse)


def chunk(id_, text, section="", method="text", confidence=1.0, updated="2026-01-01", source=None):
    from agentkit.arabic import tokenize
    return Chunk(id=id_, text=f"T{id_} › {section}\n{text}", title=f"T{id_}", section=section, source=source or f"{id_}.md", page=1,
                 lang="en", method=method, updated=updated, confidence=confidence, tokens=tokenize(text))


@pytest.fixture
def ready_env(monkeypatch, tmp_path):
    for k, v in {"ANTHROPIC_API_KEY": "sk-test", "AGENTKIT_AUTH": "jwt", "AGENTKIT_JWT_JWKS": "keys.json",
                 "AGENTKIT_LOG_KEY": "k", "AGENTKIT_LOG_SALT": "s" * 32, "AGENTKIT_EVAL_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(preflight, "KNOWLEDGE_FILES", ())
    return tmp_path


def write_reports(folder, index, live=True, rate=1.0):
    from agentkit.evals import config_fingerprint
    for name in ("eval", "heldout"):
        (folder / f"{name}-report.json").write_text(json.dumps(
            {"live": live, "pass_rate": rate, "index_version": index.version, "config": config_fingerprint()}))


def test_preflight_blocks_unverified_offline_and_unsigned(ready_env, monkeypatch, tmp_path):
    idx = Index([chunk("a", "Fines are 5 EGP a day [VERIFY].")])
    write_reports(ready_env, idx, live=False)
    res = preflight.check(idx)
    text = " ".join(res["blocking"])
    assert not res["ok"] and "[VERIFY]" in text and "offline stand-in" in text and "sign-off" in text


def test_preflight_passes_when_everything_is_in_place(ready_env, monkeypatch, tmp_path):
    from agentkit import ROOT
    rec = {"by": "Named owner", "date": "2026-10-01"}
    signoff = {"approved_by": "Head of reference", "date": "2026-10-01", "security_review": rec, "dpo": rec,
               "staff_rota": rec}
    real = ROOT / "knowledge/auc-library/signoff.json"
    original = real.read_text(encoding="utf-8")
    idx = Index([chunk("a", "Alumni can borrow 5 books.")])
    write_reports(ready_env, idx)
    try:
        real.write_text(json.dumps(signoff), encoding="utf-8")
        assert preflight.check(idx) == {"ok": True, "blocking": [], "warnings": preflight.check(idx)["warnings"]}
        write_reports(ready_env, idx, rate=0.5)
        assert any("below" in b for b in preflight.check(idx)["blocking"])
    finally:
        real.write_text(original, encoding="utf-8")


def test_proxy_identity_needs_the_proxy_secret(monkeypatch):
    monkeypatch.setenv("AGENTKIT_AUTH", "proxy")
    monkeypatch.setenv("AGENTKIT_PROXY_SECRET", "p" * 32)
    spoof = {"X-Forwarded-User": "dean@aucegypt.edu", "X-Forwarded-Groups": "staff"}
    assert authenticate(spoof).user == ""
    assert authenticate({**spoof, "X-Proxy-Secret": "p" * 32}).user


def test_unchecked_scan_is_labelled():
    idx = Index([chunk("s", "The reading room opens at 9 am on Sundays.", method="ocr", confidence=0.5)])
    ans = LibraryChat(idx, FakeLLM(), cache=None).ask("When does the reading room open on Sundays?")
    assert ans.mode == "answer" and "not checked yet" in ans.text
    idx2 = Index([chunk("s", "The reading room opens at 9 am on Sundays.", method="corrected")])
    assert "not checked" not in LibraryChat(idx2, FakeLLM(), cache=None).ask(
        "When does the reading room open on Sundays?").text


def test_different_audiences_are_not_a_conflict():
    alumni = chunk("a", "Alumni can borrow books for 14 days, up to 5 books.", updated="2024-01-01")
    students = chunk("b", "Undergraduate students can borrow books for 28 days, up to 20 books.", updated="2026-09-01")
    hits, notes = resolve_conflicts([(1.0, alumni), (0.9, students)])
    assert len(hits) == 2 and not notes


@pytest.mark.parametrize("answer", [
    "Great question! Alumni cannot borrow more than 5 books for 14 days [1]. Hope this helps!",
    "Certainly! See https://library.aucegypt.edu/services/borrow-renew-books for the 28-day rule [2].",
    "بالتأكيد! لا يمكن للخريجين استعارة أكثر من ٥ كتب لمدة ١٤ يومًا [1]. بالتوفيق!",
    "Of course. Fines are not charged on reserve items unless overdue by 2 hours [3]. Feel free to ask more.",
])
def test_style_cleanup_never_changes_facts(answer):
    import re
    out = style.clean(answer)
    keep = r"\d+|[٠-٩]+|https?://\S+|\bnot\b|cannot|unless|لا|\[\d+\]"
    assert re.findall(keep, out) == re.findall(keep, answer.split("!", 1)[-1] if answer.startswith(("Great", "Certainly", "بالتأكيد")) else answer)
    assert not style.findings(out)


def test_kill_switch_pauses_answers_and_cache(app):  # noqa: F811
    client, chat, _, _ = app
    q = {"question": "How many books can undergraduates borrow?"}
    assert client.post("/api/ask", json=q).json()["mode"] == "answer"
    assert client.post("/admin/api/maintenance", json={"on": True}).status_code in (401, 403)
    assert client.post("/admin/api/maintenance", json={"on": True}, headers=ADMIN).json() == {"maintenance": True}
    paused = client.post("/api/ask", json=q).json()
    assert paused["mode"] == "handoff" and "paused" in paused["answer"]  # the cached answer is not served
    client.post("/admin/api/maintenance", json={"on": False}, headers=ADMIN)
    assert client.post("/api/ask", json=q).json()["mode"] == "answer"


def test_eval_must_match_current_config(ready_env, monkeypatch):
    from agentkit import llm
    from agentkit.evals import config_fingerprint
    before = config_fingerprint()
    monkeypatch.setattr(llm, "MODEL_SMART", "claude-other")
    assert config_fingerprint() != before
