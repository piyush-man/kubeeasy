from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import subprocess
import time
import re
import concurrent.futures
import os


def load_k8s():
    """Load in-cluster config when running inside k8s, kubeconfig otherwise."""
    try:
        config.load_incluster_config()
        print("Using in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        print("Using local kubeconfig")


def _run(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        return ""


def get_top_pods():
    return _run(["kubectl", "top", "pods", "-A", "--no-headers"])


def parse_top_pods(raw):
    top = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            ns, pod, cpu, mem = parts[:4]
            top[(ns, pod)] = {"cpu": cpu, "memory": mem}
    return top


def get_node_info():
    disk     = _run(["df", "-h", "--output=source,size,used,avail,pcent,target"])
    nodes    = _run(["kubectl", "get", "nodes", "-o", "wide"])
    node_top = _run(["kubectl", "top", "nodes"])
    return {"disk": disk, "nodes": nodes, "node_top": node_top}


def parse_describe(raw: str) -> dict:
    d = {}

    def _get(pattern, flags=0):
        m = re.search(pattern, raw, flags)
        return m.group(1).strip() if m else None

    d["node"]        = _get(r"^Node:\s+(.+)", re.M)
    d["status"]      = _get(r"^Status:\s+(.+)", re.M)
    d["ip"]          = _get(r"^IP:\s+(.+)", re.M)
    d["qos_class"]   = _get(r"^QoS Class:\s+(.+)", re.M)
    d["service_acct"]= _get(r"^Service Account:\s+(.+)", re.M)

    restarts = re.findall(r"Restart Count:\s*(\d+)", raw)
    d["restart_count"] = sum(int(x) for x in restarts) if restarts else 0

    d["images"] = re.findall(r"^\s+Image:\s+(.+)", raw, re.M)

    lm = re.search(r"^Labels:\s*\n?(.*?)(?=\n\w)", raw, re.M | re.DOTALL)
    d["labels"] = lm.group(1).strip() if lm else ""

    am = re.search(r"^Annotations:\s*\n?(.*?)(?=\n\w)", raw, re.M | re.DOTALL)
    d["annotations"] = am.group(1).strip() if am else ""

    cm = re.search(r"Conditions:(.*?)(?=\nVolumes:|\nEvents:|\Z)", raw, re.DOTALL)
    d["conditions"] = cm.group(1).strip() if cm else ""

    rm = re.search(r"(Requests:.*?Limits:.*?)(?=\nEnvironment:|\nMounts:|\Z)", raw, re.DOTALL)
    d["resources"] = rm.group(1).strip() if rm else ""

    return d


def _collect_pod(pod, v1, top_data, node_info):
    namespace = pod.metadata.namespace
    name      = pod.metadata.name

    try:
        logs = v1.read_namespaced_pod_log(
            name=name, namespace=namespace,
            tail_lines=300, timestamps=True
        )
    except ApiException:
        logs = ""

    try:
        evts = v1.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={name}"
        )
        events = "\n".join(
            f"[{e.last_timestamp}] {e.reason}: {e.message}"
            for e in sorted(evts.items, key=lambda x: x.last_timestamp or "")
        )
    except Exception:
        events = ""

    describe_raw = _run(["kubectl", "describe", "pod", name, "-n", namespace])
    parsed   = parse_describe(describe_raw)
    top_info = top_data.get((namespace, name), {})
    phase    = (pod.status.phase or "Unknown") if pod.status else "Unknown"

    ready_conditions = []
    if pod.status and pod.status.conditions:
        ready_conditions = [f"{c.type}={c.status}" for c in pod.status.conditions]

    container_info = []
    if pod.status and pod.status.container_statuses:
        for cs in pod.status.container_statuses:
            state_str = "unknown"
            if cs.state:
                if cs.state.running:
                    state_str = "running"
                elif cs.state.waiting:
                    state_str = f"waiting({cs.state.waiting.reason})"
                elif cs.state.terminated:
                    state_str = (f"terminated({cs.state.terminated.reason},"
                                 f"exit={cs.state.terminated.exit_code})")
            container_info.append({
                "name":          cs.name,
                "ready":         cs.ready,
                "restart_count": cs.restart_count,
                "image":         cs.image,
                "state":         state_str,
            })

    return {
        "timestamp":       int(time.time()),
        "namespace":       namespace,
        "pod":             name,
        "phase":           phase,
        "conditions":      ", ".join(ready_conditions),
        "cpu":             top_info.get("cpu"),
        "memory":          top_info.get("memory"),
        "node":            parsed.get("node") or "",
        "ip":              parsed.get("ip") or "",
        "status":          parsed.get("status") or phase,
        "qos_class":       parsed.get("qos_class") or "",
        "service_account": parsed.get("service_acct") or "",
        "restart_count":   parsed.get("restart_count", 0),
        "images":          parsed.get("images") or [],
        "labels":          parsed.get("labels") or "",
        "annotations":     parsed.get("annotations") or "",
        "resources":       parsed.get("resources") or "",
        "containers":      container_info,
        "events":          events,
        "logs":            logs,
        "describe":        describe_raw,
        "node_disk":       node_info.get("disk", ""),
        "node_table":      node_info.get("nodes", ""),
        "node_top":        node_info.get("node_top", ""),
    }


def get_pod_data(max_workers=8):
    v1 = client.CoreV1Api()
    top_data  = parse_top_pods(get_top_pods())
    node_info = get_node_info()
    pods      = v1.list_pod_for_all_namespaces(watch=False).items

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_collect_pod, pod, v1, top_data, node_info): pod
            for pod in pods
        }
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                pod = futures[fut]
                print(f"  Error: {pod.metadata.namespace}/{pod.metadata.name}: {e}")
    return results
