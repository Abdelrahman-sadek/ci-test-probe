"""Vector store adapters for dense retrieval at scale (used by SqliteIndex).

    pip install -e ".[qdrant]"   AGENTKIT_QDRANT_URL=http://qdrant:6333   (or ":memory:" for tests)
"""
import os


class QdrantVectors:
    """Qdrant collection keyed by the chunk rowid, with an `access` payload filter (OWASP LLM08)."""

    def __init__(self, dim: int, url: str = "", collection: str = "agentkit"):
        from qdrant_client import QdrantClient, models
        self.models = models
        url = url or os.getenv("AGENTKIT_QDRANT_URL", ":memory:")
        self.client = QdrantClient(":memory:") if url == ":memory:" else QdrantClient(url=url)
        self.collection = collection
        if not self.client.collection_exists(collection):
            self.client.create_collection(collection, vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE))

    def upsert(self, ids: list[int], vectors: list[list[float]], access: list[str]):
        self.client.upsert(self.collection, points=[
            self.models.PointStruct(id=i, vector=v, payload={"access": a}) for i, v, a in zip(ids, vectors, access)])

    def delete(self, ids: list[int]):
        self.client.delete(self.collection, points_selector=self.models.PointIdsList(points=ids))

    def search(self, vector: list[float], k: int, access: tuple) -> list[int]:
        flt = self.models.Filter(must=[self.models.FieldCondition(
            key="access", match=self.models.MatchAny(any=list(access)))])
        hits = self.client.query_points(self.collection, query=vector, query_filter=flt, limit=k).points
        return [h.id for h in hits]
