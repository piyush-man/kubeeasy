# KubeEasy

**KubeEasy** is an AI-assisted control plane for a single Kubernetes cluster. You get a web UI to chat about workloads, inspect status, stream logs, and deploy or update applications—without juggling kubeconfig files in the browser. The backend runs **inside** the cluster and uses the pod’s **ServiceAccount** to call the Kubernetes API and tools such as `kubectl` and Helm.

The default path is **pull published images and run the install script**. Building from source is optional and documented under [`deploy/`](deploy/).

---

## Why this project exists

- **Operator-friendly** — One Helm release gives you a small footprint: UI (nginx), API (Python/FastAPI), and Qdrant for RAG-backed context.
- **No kubeconfig in the UI** — Users authenticate to the agent with a single **token**; RBAC is enforced by Kubernetes.
- **Natural-language operations** — Ask questions in plain English; the system routes between **live** `kubectl` execution and **snapshot / RAG** answers when that is faster or sufficient.
- **Composable** — The same backend exposes a WebSocket protocol so you can build other clients later; the bundled UI is a full single-page app.

---

## Architecture

```mermaid
flowchart TB
  subgraph browser [User]
    UI[Web UI]
  end
  subgraph cluster [Kubernetes cluster]
    FE[Frontend pod — nginx]
    BE[Backend pod — FastAPI]
    QD[Qdrant pod]
    SA[ServiceAccount + ClusterRole]
  end
  UI -->|HTTP / WebSocket| FE
  FE -->|"/api" "/ws" proxy| BE
  BE --> QD
  BE --> SA
  SA --> API[Kubernetes API]
```

- **Frontend** serves static assets and proxies `/ws` and `/api/` to the backend so you use **one URL** (no CORS gymnastics for the default install).
- **Backend** runs the agent, indexer, and LLM tooling; it does not store your cluster credentials—those come from the projected service account.
- **Qdrant** stores embedded snapshots of pod metadata for retrieval-augmented answers; a background indexer keeps vectors updated on a configurable interval.

---

## Prerequisites

- A Kubernetes cluster and a working kubeconfig (`kubectl` can talk to the cluster).
- **Helm 3** and **OpenSSL** on the machine where you run the installer.
- A **[Groq](https://groq.com/) API key** for the LLM (you may set it in `.env` before install **or** paste it in the UI wizard on first run if you install without it).

You do **not** need Docker on your laptop for the recommended install—you only pull images that are already published.

---

## Recommended install (published images + install script)

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/kubeeasy.git
cd kubeeasy
```

Use the upstream repository URL once it is published, or point at your fork.

### 2. Configure environment (repo root)

```bash
cp .env.example .env
```

Edit `.env` and set at least:

- **`GROQ_API_KEY`** — your Groq key (recommended before install so the AI stack is ready immediately).  
  If you omit it, the install still succeeds; the UI will offer a one-time setup step that patches the cluster Secret and restarts the backend.

**Image variables** — The defaults in `.env.example` point at published images (`docker.io/piyushman/kubeeasy-api` and `docker.io/piyushman/kubeeasy-ui`). You can leave them as-is for a standard install or override them if you publish your own registry.

### 3. Run the installer

```bash
chmod +x deploy/install.sh
./deploy/install.sh
```

The script:

- Resolves kubeconfig (supports common paths, MicroK8s, K3s, kubeadm, RKE2, or `KUBECONFIG`).
- Detects a storage class when possible (you can set `STORAGE_CLASS` in `.env`).
- Picks **NodePort** vs **LoadBalancer** for the frontend service based on cluster type unless `SERVICE_TYPE` is set in `.env`.
- Runs **`helm upgrade --install`** against [`deploy/helm/kubeeasy`](deploy/helm/kubeeasy).
- Prints a **UI URL hint** (NodePort/LB) and the **agent token**, and writes **`./.agent-connection.txt`** in the repo root for reference.

### 4. Open the UI and connect

1. Use the URL from the script output, or run:

   ```bash
   helm get notes kubeeasy -n kubeeasy
   ```

   (Use your release name and namespace if you changed `HELM_RELEASE` / `HELM_NAMESPACE` in `.env`.)

2. When the login screen appears, paste the **WebSocket URL** if you are not served via the default same-origin `/ws` path (opening the UI through the frontend service usually auto-fills this).

3. Paste the **agent token** (from the installer output or `kubectl get secret … AGENT_TOKEN` as documented in the Helm notes).

4. If you skipped `GROQ_API_KEY` in `.env`, complete the **Groq API key** step first; wait for the backend rollout to finish, then connect.

---

## What the backend does

The backend is a **FastAPI** application (`api/server.py`) built for in-cluster operation.

| Area | Behavior |
|------|----------|
| **Authentication** | After connecting on `/ws`, the first message must be the shared **agent token** (from the Helm Secret). The server compares it using a constant-time check. |
| **AI questions** | Messages with `action: "ask"` go through **`rag.query_engine.ask`**. The engine chooses **live** `kubectl` / shell flows when the question implies real-time data (logs, crashes, events, “why now”), and **RAG + LLM** over Qdrant snapshots otherwise when safe. |
| **Deployments** | The UI can trigger your shell helpers under `scripts/` (e.g. deploy, scale, remove) using parameters from the form—mirroring how operators already script Helm installs for apps. |
| **Indexing** | A **background thread** runs the RAG pipeline (`rag/rag_pipeline.py`): collect pod data in-cluster, embed text, upsert into Qdrant, prune old points—so retrieval stays close to cluster reality without blocking the API event loop. |
| **Cluster introspection** | Actions return live node, namespace, and workload information; raw **`kubectl`** passthrough is available for advanced users (with a guard against deleting the install namespace). |
| **First-run setup** | `GET /api/setup/status` and `POST /api/setup` allow supplying **Groq** when the Secret was installed with an empty key; the handler patches the Secret and triggers a **rollout restart** of the backend Deployment. |

**RBAC:** The chart binds a **ClusterRole** with broad read/write permissions within typical app namespaces so the agent can manage workloads you ask it to. Treat the **agent token** like a password and scope installs to clusters you trust.

---

## Configuration reference (`.env`)

| Variable | Purpose |
|----------|---------|
| `GROQ_API_KEY` | Groq API key (optional at install if you use the UI wizard). |
| `BACKEND_IMAGE_REPO` / `FRONTEND_IMAGE_REPO` | Container repositories; default to published `docker.io/piyushman/kubeeasy-{api,ui}`. |
| `IMAGE_TAG` | Tag for both images unless `BACKEND_IMAGE_TAG` / `FRONTEND_IMAGE_TAG` are set. |
| `HELM_NAMESPACE` / `HELM_RELEASE` | Namespace and Helm release name (defaults: `kubeeasy`). |
| `STORAGE_CLASS` | PVC storage class for Qdrant and backend data (auto-detected if unset). |
| `SERVICE_TYPE` | `NodePort` or `LoadBalancer` for the frontend Service. |
| `AGENT_TOKEN` | Optional; installer generates one if not set. |
| `KUBECONFIG` | Optional explicit kubeconfig path. |

See [`.env.example`](.env.example) for a copy-paste template.

---

## Repository layout

| Path | Description |
|------|-------------|
| `api/` | FastAPI server, WebSocket API, setup endpoints. |
| `client/` | Static web UI (served by the frontend image). |
| `rag/`, `embeddings/`, `collector/`, `vector_db/` | RAG pipeline, embeddings, Qdrant client. |
| `scripts/` | Shell automation invoked by the backend for deploy/update lifecycle. |
| `appconfig/` | Helm subchart for applications deployed *through* the UI. |
| [`deploy/`](deploy/) | **Dockerfile**, **`install.sh`**, **Helm chart** (`deploy/helm/kubeeasy`). |
| [`deploy/README.md`](deploy/README.md) | Manual Helm commands, building images from source, deeper operational notes. |

---

## Building your own images (optional)

If you change application code and need custom images, build from the repository root with:

```bash
docker build -f deploy/Dockerfile --target backend -t your-registry/kubeeasy-api:latest .
docker build -f deploy/Dockerfile --target frontend -t your-registry/kubeeasy-ui:latest .
```

Then set `BACKEND_IMAGE_REPO`, `FRONTEND_IMAGE_REPO`, and `IMAGE_TAG` in `.env` before running `./deploy/install.sh`, or pass `--set` overrides to Helm as described in [`deploy/README.md`](deploy/README.md).

---

## Troubleshooting

- **`ImagePullBackOff`** — Confirm nodes can reach Docker Hub (or set your registry mirrors / pull secrets).
- **`Pending` PVCs** — Set `STORAGE_CLASS` in `.env` to a class that exists in your cluster (`kubectl get storageclass`).
- **No external IP on LoadBalancer** — Use `SERVICE_TYPE=NodePort` in `.env` or `kubectl port-forward svc/<release>-kubeeasy-frontend 8080:80 -n <namespace>` (adjust Service name with `kubectl get svc`).
- **WebSocket fails** — Ensure you hit the **frontend** Service (proxy path `/ws`), not the backend ClusterIP directly, unless you intentionally connect with the backend port and URL.

---

**KubeEasy** — ship a cluster-aware assistant with a single Helm release and a short install script, using ready-made images by default.
