"""Thin LLM layer: Anthropic when ANTHROPIC_API_KEY is set, deterministic fake otherwise (offline tests)."""
import base64
import os
import re

MODEL_SMART = os.getenv("AGENTKIT_MODEL", "claude-sonnet-5-5")
MODEL_FAST = os.getenv("AGENTKIT_MODEL_FAST", "claude-haiku-4-5-20251001")


class LLM:
    live = False

    def complete(self, system: str, user: str, *, fast: bool = False, max_tokens: int = 1024) -> str:
        raise NotImplementedError

    def ocr_image(self, png: bytes, hint: str = "") -> str:
        raise NotImplementedError


class AnthropicLLM(LLM):
    live = True

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic()

    def complete(self, system, user, *, fast=False, max_tokens=1024):
        resp = self.client.messages.create(
            model=MODEL_FAST if fast else MODEL_SMART,
            max_tokens=max_tokens,
            # Stable system prompt first + cache_control → prompt caching cuts repeat cost.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def ocr_image(self, png, hint=""):
        resp = self.client.messages.create(
            model=MODEL_SMART,
            max_tokens=4096,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(png).decode()}},
                {"type": "text", "text": (
                    "Transcribe ALL text in this scanned page exactly, in reading order. "
                    "Keep Arabic in Arabic script and English in English; preserve headings, lists and tables "
                    "(tables as Markdown). Output only the transcription, no commentary. " + hint)},
            ]}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()


class FakeLLM(LLM):
    """Offline stand-in. Answers by quoting the top retrieved document so the pipeline is testable."""

    def __init__(self, ocr_text: str = "[fake OCR text]"):
        self.ocr_text = ocr_text
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, *, fast=False, max_tokens=1024):
        self.calls.append((system, user))
        if "No library documents matched" in system:
            return "Search strategy: combine your key concepts with AND, synonyms with OR, in English and Arabic."
        m = re.search(r'<doc id="(\d+)"[^>]*>\s*(.+?)\s*</doc>', user, re.S)
        if not m:
            return "I don't know based on the library sources I have."
        first = m.group(2).strip().splitlines()[-1][:300]
        return f"{first} [{m.group(1)}]"

    def ocr_image(self, png, hint=""):
        return self.ocr_text


def get_llm() -> LLM:
    if os.getenv("ANTHROPIC_API_KEY") and os.getenv("AGENTKIT_FAKE") != "1":
        return AnthropicLLM()
    return FakeLLM()
