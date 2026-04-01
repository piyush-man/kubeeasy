"""
Structured query handler.
All data is pre-parsed at collection time — no regex needed here.
Just dict lookups and formatting.
"""
from rag.intent import _extract_pod, _extract_namespace


def _pod(data, pod_name):
    """Find a pod by name (case-insensitive)."""
    if not pod_name:
        return None
    return next(
        (r for r in data if r.get("pod", "").lower() == pod_name.lower()),
        None
    )


def _pods_in_ns(data, namespace):
    if not namespace:
        return data
    return [r for r in data if r.get("namespace") == namespace]


# ─────────────────────────────────────────────────────────────────────────────

def structured_answer(data, query, pod_name=None, namespace=None):
    q = query.lower()

    # ── CLUSTER-WIDE ─────────────────────────────────────────────────
    if any(k in q for k in ["how many namespace", "count namespace", "list namespace",
                              "which namespace", "all namespace"]):
        nss = sorted(set(r.get("namespace") for r in data if r.get("namespace")))
        return f"Namespaces ({len(nss)} total):\n" + "\n".join(f"  • {ns}" for ns in nss)

    if any(k in q for k in ["how many pod", "count pod", "total pod",
                              "how many pods", "total pods"]):
        ns = namespace or _extract_namespace(q, data)
        subset = _pods_in_ns(data, ns)
        label = f"namespace '{ns}'" if ns else "all namespaces"
        return f"Total pods in {label}: {len(subset)}"

    if any(k in q for k in ["list pod", "which pod", "all pod", "show pod",
                              "list all pod", "get pod", "8 pods", "pods are there",
                              "what pods", "what are the pods"]):
        ns = namespace or _extract_namespace(q, data)
        subset = _pods_in_ns(data, ns)
        lines = [
            f"  • {r.get('pod'):<45} ns={r.get('namespace'):<20} "
            f"phase={r.get('phase'):<12} restarts={r.get('restart_count', 0)}"
            for r in subset
        ]
        label = f"namespace '{ns}'" if ns else "all namespaces"
        return f"Pods in {label} ({len(lines)}):\n" + "\n".join(lines)

    # ── RESOURCE RANKING (most/least/highest/top resource usage) ─────
    if any(k in q for k in ["most resource", "highest resource", "top resource",
                              "most cpu", "highest cpu", "most memory", "highest memory",
                              "taking lot", "using most", "consuming most",
                              "resource usage", "top pod", "heavy pod"]):
        def _parse_mi(val):
            """Parse '3Mi' or '120m' → float for sorting."""
            if not val:
                return 0.0
            val = str(val).strip()
            if val.endswith("Mi"):
                return float(val[:-2])
            if val.endswith("Ki"):
                return float(val[:-2]) / 1024
            if val.endswith("Gi"):
                return float(val[:-2]) * 1024
            if val.endswith("m"):   # millicores
                return float(val[:-1]) / 1000
            try:
                return float(val)
            except ValueError:
                return 0.0

        sort_by_mem = any(k in q for k in ["memory", "mem", "ram"])
        key_fn = (lambda r: _parse_mi(r.get("memory"))) if sort_by_mem \
                 else (lambda r: _parse_mi(r.get("cpu")))

        ranked = sorted(data, key=key_fn, reverse=True)
        metric = "Memory" if sort_by_mem else "CPU"

        lines = [
            f"  {i+1}. {r.get('pod'):<45} ns={r.get('namespace'):<20} "
            f"CPU={r.get('cpu') or 'N/A':<8} Memory={r.get('memory') or 'N/A'}"
            for i, r in enumerate(ranked[:10])
        ]
        note = "(N/A means metrics-server has no data for that pod)" \
               if any(r.get(metric.lower()) is None for r in ranked[:5]) else ""
        return f"Pods ranked by {metric} usage (highest first):\n" + \
               "\n".join(lines) + (f"\n\n{note}" if note else "")

    # ── PER-POD QUERIES ───────────────────────────────────────────────
    pod = _pod(data, pod_name)

    if not pod:
        # If no pod identified but we have a namespace, list that NS
        ns = namespace or _extract_namespace(q, data)
        if ns:
            subset = _pods_in_ns(data, ns)
            lines = [f"  • {r.get('pod')}" for r in subset]
            return f"Pods in '{ns}':\n" + "\n".join(lines)
        return None   # fall through to LLM

    p = pod.get

    # STATUS / PHASE
    if any(k in q for k in ["status", "phase", "running", "ready", "healthy"]):
        containers = p("containers") or []
        c_lines = "\n".join(
            f"  {c['name']}: {c['state']} (restarts={c['restart_count']})"
            for c in containers
        )
        return (
            f"Pod:        {p('pod')} ({p('namespace')})\n"
            f"Phase:      {p('phase')}\n"
            f"Status:     {p('status')}\n"
            f"Conditions: {p('conditions')}\n"
            f"Node:       {p('node') or 'unknown'}\n\n"
            f"Containers:\n{c_lines or '  none'}\n\n"
            f"Events:\n{(p('events') or 'none')[:600]}"
        )

    # RESTART
    if "restart" in q:
        containers = p("containers") or []
        c_lines = "\n".join(
            f"  {c['name']}: {c['restart_count']} restarts (state={c['state']})"
            for c in containers
        )
        events = (p("events") or "none")[:800]
        return (
            f"Pod: {p('pod')} | Total restarts: {p('restart_count', 0)}\n\n"
            f"Per container:\n{c_lines}\n\n"
            f"Events (may explain cause):\n{events}"
        )

    # IMAGE
    if "image" in q:
        images   = p("images") or []
        from_containers = [(c["name"], c["image"]) for c in (p("containers") or [])]
        lines = [f"  {name}: {img}" for name, img in from_containers] or \
                [f"  {img}" for img in images]
        return f"Pod: {p('pod')}\nContainer images:\n" + "\n".join(lines)

    # LABELS
    if "label" in q:
        return f"Pod: {p('pod')}\nLabels:\n{p('labels') or '  none'}"

    # ANNOTATIONS
    if "annotation" in q:
        return f"Pod: {p('pod')}\nAnnotations:\n{p('annotations') or '  none'}"

    # NODE
    if any(k in q for k in ["node", "running on", "where is", "where are", "scheduled"]):
        return (
            f"Pod '{p('pod')}' is scheduled on node: {p('node') or 'unknown'}\n"
            f"Pod IP: {p('ip') or 'unknown'}\n"
            f"Namespace: {p('namespace')}"
        )

    # RESOURCES
    if any(k in q for k in ["resource", "limit", "request", "quota"]):
        return (
            f"Pod: {p('pod')}\n"
            f"CPU:    {p('cpu') or 'N/A (metrics-server?)'}\n"
            f"Memory: {p('memory') or 'N/A'}\n\n"
            f"Resource requests/limits:\n{p('resources') or '  Not configured'}\n"
            f"QoS Class: {p('qos_class') or 'unknown'}"
        )

    # CPU / MEMORY usage
    if any(k in q for k in ["cpu", "memory", "usage", "utilization", "how much"]):
        return (
            f"Pod: {p('pod')} ({p('namespace')})\n"
            f"CPU:    {p('cpu') or 'N/A'}\n"
            f"Memory: {p('memory') or 'N/A'}\n"
            f"QoS:    {p('qos_class') or 'unknown'}"
        )

    # SERVICE ACCOUNT
    if "service account" in q:
        return f"Pod: {p('pod')}\nService Account: {p('service_account') or 'default'}"

    # IP
    if any(k in q for k in ["ip", "ip address"]):
        return f"Pod: {p('pod')}\nPod IP: {p('ip') or 'unknown'}"

    # EVENTS
    if "event" in q:
        return f"Events for pod '{p('pod')}':\n{p('events') or 'No events found'}"

    return None   # fall through to LLM


def cluster_wide_summary(data):
    """Fast structured cluster-health summary, no LLM."""
    total    = len(data)
    nss      = set(r.get("namespace") for r in data)
    running  = sum(1 for r in data if r.get("phase") == "Running")
    pending  = sum(1 for r in data if r.get("phase") == "Pending")
    failed   = sum(1 for r in data if r.get("phase") in ("Failed", "Unknown"))
    crashing = [r for r in data if (r.get("restart_count") or 0) > 0]

    lines = [
        f"Cluster summary",
        f"  Namespaces : {len(nss)}",
        f"  Total pods : {total}",
        f"  Running    : {running}",
        f"  Pending    : {pending}",
        f"  Failed/Unknown: {failed}",
    ]

    if crashing:
        lines.append("\nPods with restarts:")
        for r in sorted(crashing, key=lambda x: -(x.get("restart_count") or 0))[:10]:
            lines.append(
                f"  • {r.get('pod'):<45} ns={r.get('namespace'):<20} "
                f"restarts={r.get('restart_count')}"
            )

    return "\n".join(lines)


def logs_answer(data, pod_name):
    """Return raw logs for a pod — no LLM needed."""
    pod = _pod(data, pod_name)
    if not pod:
        return f"Pod '{pod_name}' not found in the index."
    logs = (pod.get("logs") or "No logs available").strip()
    return f"Logs for '{pod.get('pod')}' (last 300 lines):\n{logs}"
