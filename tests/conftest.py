import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agentkit.chat import LibraryChat  # noqa: E402
from agentkit.llm import FakeLLM  # noqa: E402
from agentkit.rag import ingest  # noqa: E402

SEED = ROOT / "knowledge/auc-library/pages"
FIXTURES = ROOT / "samples/fixtures"


@pytest.fixture(scope="session")
def seed_index():
    return ingest([SEED], FakeLLM())[0]


@pytest.fixture
def chat(seed_index):
    return LibraryChat(seed_index, FakeLLM())


@pytest.fixture(scope="session")
def pdfs(tmp_path_factory):
    import make_samples
    return make_samples.make(tmp_path_factory.mktemp("pdf"))
