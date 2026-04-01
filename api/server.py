"""
KubeManager Agent API — single cluster, single token, no kubeconfig.
The pod's ServiceAccount provides all Kubernetes access automatically.

WS /ws    — all operations (auth → action → response)
GET /healthz — liveness probe
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import os, secrets, json, subprocess, asyncio, traceback

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from rag.query_engine import ask

app = FastAPI(title="KubeManager", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_TOKEN     = os.getenv("AGENT_TOKEN", "")
SCRIPT_DIR = os.getenv("SCRIPT_DIR", "/app/scripts")
INDEX_FILE = "/data/index.json"
os.makedirs("/data", exist_ok=True)

# ── index (tracks deployed apps) ──────────────────────────────────────

def _load_index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE) as f:
            return json.load(f)
    return {}

def _save_index(data):
    with open(INDEX_FILE, "w") as f:
        json.dump(data, f)

# ── kubectl / helm via in-cluster ServiceAccount ──────────────────────

def _kube(*args, timeout=30):
    """Run kubectl using the in-cluster ServiceAccount — no KUBECONFIG needed."""
    r = subprocess.run(["kubectl", *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode

def _run_script(script, args, timeout=180):
    """Run a shell script from SCRIPT_DIR."""
    cmd = ["bash", os.path.join(SCRIPT_DIR, script)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout + r.stderr

# ── status ────────────────────────────────────────────────────────────

def _get_status():
    index = _load_index()
    result = {}
    for ns, apps in index.items():
        result[ns] = {}
        for name, info in apps.items():
            phase, _  = _kube("get", "pods", "-n", ns,
                               "-l", f"app.kubernetes.io/name={name}",
                               "-o=jsonpath={.items[0].status.phase}")
            ready, _  = _kube("get", "deployment", name, "-n", ns,
                               "-o=jsonpath={.status.readyReplicas}")
            result[ns][name] = {
                **info,
                "status":        phase or "Not Deployed",
                "replicas_ready": ready or "0",
            }
    return result

# ── cluster info (pulled live from API — no config file) ──────────────

def _cluster_info():
    version_out, _ = _kube("version", "--short", "--output=json")
    nodes_out, _   = _kube("get", "nodes",
                            "-o=jsonpath={range .items[*]}{.metadata.name},"
                            "{.status.conditions[-1].type},"
                            "{.status.conditions[-1].status}\\n{end}")
    ns_out, _      = _kube("get", "namespaces",
                            "-o=jsonpath={.items[*].metadata.name}")
    return {
        "nodes":      [l for l in nodes_out.splitlines() if l],
        "namespaces": ns_out.split(),
        "version":    version_out,
    }

# ── WebSocket ─────────────────────────────────────────────────────────

@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_main(ws: WebSocket):
    await ws.accept()
    try:
        # ── auth handshake ────────────────────────────────────────────
        token = await ws.receive_text()
        if not _TOKEN or not secrets.compare_digest(token.strip(), _TOKEN):
            await ws.send_text(json.dumps({"type": "error", "msg": "Invalid token"}))
            await ws.close(code=4001)
            return

        # Send cluster info on successful auth
        info = _cluster_info()
        await ws.send_text(json.dumps({
            "type": "auth",
            "msg":  "AUTH_OK",
            "cluster": info,
        }))

        loop = asyncio.get_event_loop()

        # ── message loop ──────────────────────────────────────────────
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send_text(json.dumps({"type":"error","msg":"Invalid JSON"}))
                continue

            action = msg.get("action", "")

            # ── AI ────────────────────────────────────────────────────
            if action == "ask":
                q = msg.get("question", "").strip()
                if not q:
                    await _send(ws, {"type":"answer","text":"Empty question."})
                    continue
                try:
                    answer = await loop.run_in_executor(None, ask, q)
                    await _send(ws, {"type":"answer","text":answer})
                except Exception as e:
                    await _send(ws, {"type":"answer","text":f"Error: {e}"})

            # ── cluster info (refresh) ────────────────────────────────
            elif action == "cluster_info":
                await _send(ws, {"type":"cluster_info","data":_cluster_info()})

            # ── list all pods (status tab) ────────────────────────────
            elif action == "get_status":
                apps = await loop.run_in_executor(None, _get_status)
                await _send(ws, {"type":"status","apps":apps})

            # ── deploy ────────────────────────────────────────────────
            elif action == "deploy":
                d = msg.get("data", {})
                args = _build_deploy_args(d)
                out  = await loop.run_in_executor(None, _run_script, "kubedeploy.sh", args)
                if "success" in out.lower():
                    await _send(ws, {"type":"ok","msg":"Deployed successfully ✓"})
                else:
                    await _send(ws, {"type":"error","msg":out.strip()})

            # ── update ────────────────────────────────────────────────
            elif action == "update":
                d    = msg.get("data", {})
                args = _build_deploy_args(d, update=True)
                out  = await loop.run_in_executor(None, _run_script, "kubeupdate.sh", args)
                if "success" in out.lower():
                    await _send(ws, {"type":"ok","msg":"Updated successfully ✓"})
                else:
                    await _send(ws, {"type":"error","msg":out.strip()})

            # ── app actions (start/stop/restart/remove) ───────────────
            elif action == "app_action":
                act  = msg.get("app_action","")
                ns   = msg.get("namespace","")
                name = msg.get("app_name","")
                scripts = {
                    "start":   "kubestart.sh",
                    "stop":    "kubestop.sh",
                    "restart": "kuberestart.sh",
                    "remove":  "kuberemove.sh",
                }
                if act not in scripts:
                    await _send(ws, {"type":"error","msg":f"Unknown action: {act}"})
                    continue
                args = ["--namespace", ns, "--name", name]
                out  = await loop.run_in_executor(None, _run_script, scripts[act], args)
                await _send(ws, {"type":"ok","msg":out.strip()})

            # ── logs ──────────────────────────────────────────────────
            elif action == "get_logs":
                ns   = msg.get("namespace","")
                name = msg.get("app_name","")
                pod, _ = _kube("get","pods","-n",ns,
                                "-l",f"app.kubernetes.io/name={name}",
                                "-o=jsonpath={.items[0].metadata.name}")
                if not pod:
                    await _send(ws, {"type":"logs","pod":"","text":"No pod found."})
                    continue
                logs, _ = _kube("logs", pod, "-n", ns, "--tail=300")
                await _send(ws, {"type":"logs","pod":pod,"text":logs})

            # ── raw kubectl (power users) ─────────────────────────────
            elif action == "kubectl":
                cmd_args = msg.get("args", [])
                # block destructive ops on kubemanager namespace
                if "delete" in cmd_args and "kubemanager" in " ".join(cmd_args):
                    await _send(ws, {"type":"error","msg":"Cannot delete kubemanager namespace."})
                    continue
                out, rc = _kube(*cmd_args, timeout=30)
                await _send(ws, {"type":"kubectl_result","output":out,"rc":rc})

            else:
                await _send(ws, {"type":"error","msg":f"Unknown action: {action}"})

    except WebSocketDisconnect:
        pass
    except Exception:
        traceback.print_exc()


# ── helpers ───────────────────────────────────────────────────────────

async def _send(ws, obj):
    await ws.send_text(json.dumps(obj))


def _build_deploy_args(d, update=False):
    flag_map = {
        "websiteName": "name",
        "appPort":     "port",
        "dockerImage": "image",
        "imageTag":    "version",
        "minCpu":      "mincpu",
        "maxCpu":      "maxcpu",
        "minMemory":   "minmem",
        "maxMemory":   "maxmem",
        "maxPercentage": "percentage",
        "minReplicas": "minreplicas",
        "maxReplicas": "maxreplicas",
    }
    keys = ["websiteName","namespace","replicas","appPort","dockerImage",
            "imageTag","autoscaling","minCpu","maxCpu","minMemory","maxMemory",
            "maxPercentage","minReplicas","maxReplicas","nfsip"]
    args = []
    for k in keys:
        v = d.get(k, "")
        if v:
            args += [f"--{flag_map.get(k, k.lower())}", str(v)]
    for mp, pp in zip(d.get("mountPaths",[]), d.get("podPaths",[])):
        if mp and pp:
            args += ["--mount", mp, pp]
    return args


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
