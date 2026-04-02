import sys
import time
import os
from collector.collector import load_k8s, get_pod_data
from processor.preprocess import clean_text
from embeddings.embedder import get_embeddings
from vector_db.qdrant_client import ensure_collection, purge_and_recreate, insert_embeddings, delete_old_data


def run_pipeline():
    t0 = time.time()
    try:
        print("Collecting pod data (parallel)...")
        raw_data = get_pod_data()
        print(f"  {len(raw_data)} pods collected in {time.time()-t0:.1f}s")

        processed = clean_text(raw_data)
        texts    = [p["text"]     for p in processed]
        metadata = [p["metadata"] for p in processed]

        t1 = time.time()
        print("Embedding...")
        embeddings = get_embeddings(texts)
        print(f"  Embedded in {time.time()-t1:.1f}s")

        print("Upserting to Qdrant...")
        insert_embeddings(embeddings, metadata)

        print("Pruning old records...")
        delete_old_data(days=7)

        print(f"Pipeline done — {len(texts)} pods indexed. Total: {time.time()-t0:.1f}s\n")

    except Exception as e:
        import traceback
        print(f"Pipeline error: {e}")
        traceback.print_exc()


def pipeline_daemon(interval_seconds: float | None = None, purge_on_start: bool = False) -> None:
    """Long-running RAG indexer (in-cluster). Safe to run in a background thread on the API pod."""
    interval_seconds = interval_seconds or float(os.getenv("PIPELINE_INTERVAL_SECONDS", "15"))
    load_k8s()

    if purge_on_start:
        print("Purging old collection and recreating...")
        purge_and_recreate()
    else:
        ensure_collection()

    while True:
        print("=== Running pipeline ===")
        t_start = time.time()
        run_pipeline()
        elapsed = time.time() - t_start
        wait = max(0.0, interval_seconds - elapsed)
        if wait > 0:
            print(f"Next run in {wait:.1f}s...\n")
            time.sleep(wait)
        else:
            print(f"Pipeline took {elapsed:.1f}s, running immediately\n")


if __name__ == "__main__":
    purge = "--purge" in sys.argv or "--purge-on-start" in sys.argv
    pipeline_daemon(purge_on_start=purge)
