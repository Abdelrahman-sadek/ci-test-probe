"""Background ingestion jobs: staff uploads and re-crawls are indexed without blocking the chat server.
One worker thread; the index lock keeps searches consistent while a job writes."""
import queue
import threading
import time
import uuid
from pathlib import Path

from .rag import BaseIndex, ingest


class JobQueue:
    def __init__(self, index: BaseIndex, llm, save_path: str | Path, review: bool = True,
                 contextualize: str | bool = False):
        self.index, self.llm, self.save_path = index, llm, str(save_path)
        self.review, self.contextualize = review, contextualize
        self.jobs: dict[str, dict] = {}
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="agentkit-ingest").start()

    def submit(self, paths: list[str], review: bool | None = None, force: bool = False) -> str:
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = {"id": jid, "status": "queued", "paths": [str(p) for p in paths], "report": [],
                          "created": time.time()}
        self.q.put((jid, paths, self.review if review is None else review, force))
        return jid

    def get(self, jid: str) -> dict | None:
        return self.jobs.get(jid)

    def wait(self, jid: str, timeout: float = 60.0) -> dict:
        end = time.time() + timeout
        while time.time() < end and self.jobs[jid]["status"] in ("queued", "running"):
            time.sleep(0.05)
        return self.jobs[jid]

    def _worker(self):
        while True:
            jid, paths, review, force = self.q.get()
            job = self.jobs[jid]
            job["status"] = "running"
            try:
                self.index.snapshot(self.save_path)  # one-step rollback if a bad batch slips through
                _, report = ingest(paths, self.llm, self.index, contextualize=self.contextualize, review=review,
                                   force=force, checkpoint=lambda ix: ix.save(self.save_path))
                self.index.save(self.save_path)
                job.update(status="done", report=report)
            except Exception as e:  # noqa: BLE001 — surface any failure in the job record
                job.update(status="failed", error=f"{type(e).__name__}: {e}")
            job["finished"] = time.time()
