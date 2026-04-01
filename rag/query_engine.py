"""
Query engine — decides whether to use live command execution or RAG snapshot.

Flow:
  classify intent
    → LIVE_EXEC   : questions needing real-time data (restarts, logs, events, status)
                    → plan kubectl commands → execute → synthesize answer
    → STRUCTURED  : dict lookup from snapshot (fast, <5ms)
    → LOG         : raw log return
    → CLUSTER_WIDE: cluster summary
    → DIAGNOSTIC  : RAG + LLM from snapshot
"""
from rag.intent import classify, STRUCTURED, LOG, DIAGNOSTIC, CLUSTER_WIDE
from rag.structured_query import structured_answer, logs_answer
from rag.retriever import search
from rag.llm import generate_answer, execute_and_answer
from vector_db.qdrant_client import client, COLLECTION_NAME

# Queries that MUST use live execution (can't be answered from a snapshot)
_LIVE_KEYWORDS = {
    "why", "restart", "restarted", "crash", "crashed", "crashloop", "oom",
    "killed", "evict", "evicted", "fail", "failing", "failed", "error", "errors",
    "pending", "not ready", "not running", "problem", "issue", "debug", "diagnose",
    "investigate", "root cause", "what happened", "went wrong", "unhealthy",
    "events", "event", "recent", "latest", "current", "live", "now",
    "resource usage", "cpu usage", "memory usage", "top", "consuming",
    "log", "logs", "output", "stdout", "stderr",
}

def _needs_live(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _LIVE_KEYWORDS)


def _get_all_data(limit: int = 2000) -> list:
    try:
        records, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=limit,
            with_payload=True
        )
        seen = {}
        for r in records:
            p = r.payload
            key = (p.get("namespace"), p.get("pod"))
            if key not in seen or p.get("timestamp", 0) > seen[key].get("timestamp", 0):
                seen[key] = p
        return list(seen.values())
    except Exception:
        return []


def _build_context(results: list) -> str:
    blocks = []
    for r in results:
        containers = r.get("containers") or []
        c_lines = "\n".join(
            f"    {c['name']}: state={c['state']} restarts={c['restart_count']} image={c['image']}"
            for c in containers
        )
        blocks.append(f"""=== {r.get('namespace')}/{r.get('pod')} ===
Phase:      {r.get('phase')}  |  Status: {r.get('status')}
Node:       {r.get('node')}   |  IP: {r.get('ip')}
CPU: {r.get('cpu') or 'N/A'}  |  Memory: {r.get('memory') or 'N/A'}
Restarts:   {r.get('restart_count', 0)}
QoS:        {r.get('qos_class')}

Containers:
{c_lines or '  (none)'}

Resource limits:
{(r.get('resources') or '  not set')[:400]}

Events:
{(r.get('events') or '  none')[:1000]}

Logs (recent):
{(r.get('logs') or '  none')[-1500:]}

kubectl describe (excerpt):
{(r.get('describe') or '  none')[:1200]}
""")
    return "\n\n".join(blocks)


def ask(query: str) -> str:
    all_data = _get_all_data()

    # ── Live execution path: always use for diagnostic/real-time questions ──
    if _needs_live(query):
        # Provide snapshot context as a hint so the LLM knows pod/namespace names
        context_hint = ""
        if all_data:
            intent, pod_name, namespace = classify(query, all_data)
            try:
                results = search(query, top_k=3, namespace=namespace)
                if pod_name:
                    from rag.structured_query import _pod
                    target = _pod(all_data, pod_name)
                    if target and target not in results:
                        results.insert(0, target)
                if results:
                    context_hint = _build_context(results[:3])
            except Exception:
                pass
        return execute_and_answer(query, context_hint)

    # ── No snapshot data at all → fall back to live ─────────────────────────
    if not all_data:
        return execute_and_answer(query)

    intent, pod_name, namespace = classify(query, all_data)

    # ── Fast structured paths ─────────────────────────────────────────────
    if intent == CLUSTER_WIDE:
        ans = structured_answer(all_data, query, pod_name, namespace)
        if ans:
            return ans
        # Cluster-wide diagnostic → live
        return execute_and_answer(query)

    if intent == LOG:
        return logs_answer(all_data, pod_name)

    if intent == STRUCTURED:
        ans = structured_answer(all_data, query, pod_name, namespace)
        if ans:
            return ans

    # ── RAG + LLM from snapshot ───────────────────────────────────────────
    results = search(query, top_k=5, namespace=namespace)
    if pod_name:
        from rag.structured_query import _pod
        target = _pod(all_data, pod_name)
        if target and target not in results:
            results.insert(0, target)

    if not results:
        # No RAG results → try live
        return execute_and_answer(query)

    context = _build_context(results)
    return generate_answer(context, query)
