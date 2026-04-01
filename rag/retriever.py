import os
from embeddings.embedder import get_embedding
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from vector_db.qdrant_client import COLLECTION_NAME

client = QdrantClient(
    os.getenv("QDRANT_HOST", "localhost"),
    port=int(os.getenv("QDRANT_PORT", "6333"))
)


def search(query: str, top_k: int = 5, namespace: str = None) -> list:
    """
    Semantic search. Optionally filter by namespace for precision.
    """
    vector = get_embedding(query)

    query_filter = None
    if namespace:
        query_filter = Filter(
            must=[FieldCondition(key="namespace", match=MatchValue(value=namespace))]
        )

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    return [p.payload for p in results.points]
