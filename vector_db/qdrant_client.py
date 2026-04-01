from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance,
    Filter, FieldCondition, Range,
    PayloadSchemaType
)
from datetime import datetime, timedelta
import uuid
import time
import os

_host = os.getenv("QDRANT_HOST", "localhost")
_port = int(os.getenv("QDRANT_PORT", "6333"))

client = QdrantClient(_host, port=_port)
COLLECTION_NAME = "k8s_logs"


def ensure_collection():
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        )
        for field in ("namespace", "pod", "phase", "node", "timestamp"):
            client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD
                    if field != "timestamp" else PayloadSchemaType.INTEGER,
            )
        print(f"Created collection + indexes: {COLLECTION_NAME}")


def purge_and_recreate():
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        client.delete_collection(COLLECTION_NAME)
        print(f"Dropped collection: {COLLECTION_NAME}")
    ensure_collection()


def _make_id(payload):
    # Stable per pod — upsert always replaces, never duplicates
    key = f"{payload.get('namespace')}-{payload.get('pod')}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def insert_embeddings(vectors, payloads):
    now = int(time.time())
    points = []
    for vec, payload in zip(vectors, payloads):
        payload.setdefault("timestamp", now)
        if "containers" in payload:
            payload["containers"] = [
                {k: str(v) for k, v in c.items()} for c in payload["containers"]
            ]
        points.append({
            "id":      _make_id(payload),
            "vector":  vec.tolist(),
            "payload": payload,
        })
    client.upsert(collection_name=COLLECTION_NAME, points=points)


def delete_old_data(days=7):
    cutoff = (datetime.utcnow() - timedelta(days=days)).timestamp()
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="timestamp", range=Range(lt=cutoff))]
        ),
    )
