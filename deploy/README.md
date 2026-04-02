# Deploying KubeEasy

This folder holds everything needed to **build container images** and **install on Kubernetes** with Helm. Run Docker commands from the **repository root** so paths like `client/` and `requirements.txt` resolve correctly.

## What gets deployed

Helm installs **three pods** in your chosen namespace:

1. **Backend** — Python/FastAPI: WebSocket agent, `kubectl`/Helm, RAG indexer (background thread), optional first-run Groq setup API.
2. **Frontend** — nginx: static UI plus reverse proxy to the backend for `/ws` and `/api/`.
3. **Qdrant** — vector database for RAG snapshots.

Persistent volumes back Qdrant and a small backend data PVC for local index state.

## 1. Prerequisites

- Docker (for building images)
- `kubectl` configured for the target cluster
- Helm 3
- A Groq API key (or install without it and set it later in the UI)

## 2. Build images

From the **repo root**:

```bash
docker build -f deploy/Dockerfile --target backend -t YOUR_REGISTRY/kubeeasy-api:latest .
docker build -f deploy/Dockerfile --target frontend -t YOUR_REGISTRY/kubeeasy-ui:latest .
docker push YOUR_REGISTRY/kubeeasy-api:latest
docker push YOUR_REGISTRY/kubeeasy-ui:latest
```

Defaults in `deploy/helm/kubeeasy/values.yaml` use `docker.io/piyushman/kubeeasy-api` and `…/kubeeasy-ui`; override with `--set` if you use your own registry.

## 3. Install with Helm

Still from **repo root** (chart path is under `deploy/`):

```bash
export TOKEN="$(openssl rand -hex 32)"
# Optional: export GROQ_KEY="gsk_…" and pass --set groqApiKey="$GROQ_KEY"

helm upgrade --install kubeeasy ./deploy/helm/kubeeasy \
  -n kubeeasy --create-namespace \
  --set agentToken="$TOKEN" \
  --set groqApiKey="${GROQ_KEY:-}" \
  --set backend.image.repository=YOUR_REGISTRY/kubeeasy-api \
  --set backend.image.tag=latest \
  --set frontend.image.repository=YOUR_REGISTRY/kubeeasy-ui \
  --set frontend.image.tag=latest \
  --set qdrant.storageClassName=standard
```

Use a storage class that exists on your cluster (`kubectl get storageclass`). On **NodePort** vs **LoadBalancer**, set `frontend.serviceType` if the default in `values.yaml` does not fit (local clusters often use `NodePort`; many clouds use `LoadBalancer`).

After install:

```bash
helm get notes kubeeasy -n kubeeasy
```

That prints how to reach the **UI URL** and how to read the **agent token** from the Kubernetes Secret.

## 4. Optional: helper script

`deploy/install.sh` detects kubeconfig, storage class, and runs `helm upgrade --install` with sensible defaults. It loads optional variables from **`.env` in the repo root** (not under `deploy/`). Example root `.env`:

```bash
GROQ_API_KEY=gsk_…
BACKEND_IMAGE_REPO=your-registry/kubeeasy-api
FRONTEND_IMAGE_REPO=your-registry/kubeeasy-ui
```

Run:

```bash
./deploy/install.sh
```

Connection hints are written to `.agent-connection.txt` in the repo root.

## 5. First-time UI setup

1. Open the UI using the URL from Helm notes (NodePort/LB or `kubectl port-forward` to the **frontend** Service).
2. The browser talks to the same host; WebSocket path is `/ws` (proxied by nginx).
3. Paste the **agent token** from the Secret (see `helm get notes`).
4. If you installed with an empty `groqApiKey`, the UI shows a **Groq API key** step; saving it patches the Secret and restarts the backend—wait until the new pod is ready, then connect again.

## Files in this directory

| File / directory | Role |
|------------------|------|
| `Dockerfile` | Multi-stage: `frontend` (nginx + `client/`) and `backend` (Python API) |
| `nginx.minimal.conf` | Default nginx config inside the UI image; Helm mounts a generated config in-cluster for proxying to the API |
| `install.sh` | Optional Helm installer |
| `helm/kubeeasy/` | Helm chart: Deployments, Services, RBAC, PVCs, nginx ConfigMap |
