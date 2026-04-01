"""
Intent classifier — no LLM call, pure heuristics.
Decides: STRUCTURED | DIAGNOSTIC | LOG | CLUSTER_WIDE | UNKNOWN
Returns: (intent, pod_name_or_None, namespace_or_None)
"""
import re

# ── intent categories ─────────────────────────────────────────────────────────
STRUCTURED   = "structured"   # answer directly from payload, no LLM
DIAGNOSTIC   = "diagnostic"   # RAG + LLM analysis
LOG          = "log"          # return raw logs, no LLM
CLUSTER_WIDE = "cluster_wide" # whole-cluster summary, no single pod

_STRUCTURED_KEYWORDS = {
    "how many", "count", "list", "which", "all pods", "all namespace",
    "show pods", "show namespace", "image", "images", "label", "labels",
    "annotation", "annotations", "restart", "restarts", "node", "running on",
    "where is", "where are", "status", "phase", "ip address", "service account",
    "qos", "resource", "cpu", "memory", "limit", "request",
    # resource ranking
    "most resource", "highest resource", "top resource", "most cpu", "highest cpu",
    "most memory", "highest memory", "taking lot", "using most", "consuming most",
    "top pod", "heavy pod",
    # natural list phrasings
    "pods are there", "what pods", "what are the pods", "tell me the pods",
    "show me the pods", "how many pods",
}

_DIAGNOSTIC_KEYWORDS = {
    "why", "what happened", "error", "crash", "oom", "killed", "fail",
    "issue", "problem", "debug", "diagnose", "not working", "not ready",
    "pending", "evicted", "backoff", "cannot", "unable", "investigate",
    "health", "unhealthy",
}

_LOG_KEYWORDS = {"log", "logs", "output", "stdout", "stderr", "print"}

_CLUSTER_KEYWORDS = {
    "cluster", "overall", "summary", "health of cluster", "all namespaces",
    "namespace count", "total pods", "how many pods", "how many namespace",
}


def classify(query: str, all_data: list) -> tuple:
    q = query.lower().strip()

    pod_name = _extract_pod(q, all_data)
    namespace = _extract_namespace(q, all_data)

    # cluster-wide first
    if any(k in q for k in _CLUSTER_KEYWORDS):
        return CLUSTER_WIDE, pod_name, namespace

    # log fetch (fast path — just return raw logs)
    if any(k in q for k in _LOG_KEYWORDS) and pod_name:
        return LOG, pod_name, namespace

    # structured lookup
    if any(k in q for k in _STRUCTURED_KEYWORDS):
        return STRUCTURED, pod_name, namespace

    # diagnostic / reasoning
    if any(k in q for k in _DIAGNOSTIC_KEYWORDS):
        return DIAGNOSTIC, pod_name, namespace

    # fallback: if a pod name is in the query, go structured first
    if pod_name:
        return STRUCTURED, pod_name, namespace

    return DIAGNOSTIC, pod_name, namespace


def _extract_pod(query: str, data: list) -> str | None:
    pods = sorted(
        [r.get("pod", "") for r in data if r.get("pod")],
        key=len, reverse=True   # longest first avoids prefix matches
    )
    for pod in pods:
        if pod.lower() in query:
            return pod
    return None


def _extract_namespace(query: str, data: list) -> str | None:
    namespaces = sorted(
        list(set(r.get("namespace", "") for r in data if r.get("namespace"))),
        key=len, reverse=True
    )
    for ns in namespaces:
        if ns.lower() in query:
            return ns
    return None
