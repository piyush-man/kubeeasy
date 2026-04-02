#!/bin/bash
set -e
BOLD="\033[1m"; GREEN="\033[32m"; CYAN="\033[36m"; RED="\033[31m"; YELLOW="\033[33m"; RESET="\033[0m"

echo -e "${BOLD}${CYAN}"
echo "  ██╗  ██╗██╗   ██╗██████╗ ███████╗"
echo "  ██║ ██╔╝██║   ██║██╔══██╗██╔════╝"
echo "  █████╔╝ ██║   ██║██████╔╝█████╗  "
echo "  ██╔═██╗ ██║   ██║██╔══██╗██╔══╝  "
echo "  ██║  ██╗╚██████╔╝██████╔╝███████╗"
echo "  ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝"
echo -e "  KubeEasy — Helm installer${RESET}"
echo ""

for cmd in kubectl helm openssl; do
  command -v "$cmd" &>/dev/null || { echo -e "${RED}ERROR: '$cmd' not found in PATH.${RESET}"; exit 1; }
done

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
NS="${HELM_NAMESPACE:-kubeeasy}"
RELEASE="${HELM_RELEASE:-kubeeasy}"
CHART_DIR="$DEPLOY_DIR/helm/kubeeasy"
ENV_FILE="$REPO_ROOT/.env"
GROQ_SET=()

if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
fi

# Groq optional here if you prefer the in-browser setup wizard (empty Helm value).
if [ -n "$GROQ_API_KEY" ] && [ "$GROQ_API_KEY" != "your_groq_api_key_here" ]; then
  GROQ_SET=(--set "groqApiKey=$GROQ_API_KEY")
else
  echo -e "${YELLOW}No GROQ_API_KEY in .env — installing without it. Use the UI wizard to save your Groq key after install.${RESET}"
  GROQ_SET=(--set 'groqApiKey=')
fi

echo -e "${BOLD}Detecting Kubernetes cluster…${RESET}"
TMPKUBE=$(mktemp /tmp/kubeconfig.XXXXXX)
trap "rm -f $TMPKUBE" EXIT

if [ -n "$KUBECONFIG" ] && [ -f "$KUBECONFIG" ]; then
  cp "$KUBECONFIG" "$TMPKUBE"
elif command -v microk8s &>/dev/null; then
  microk8s config > "$TMPKUBE"
elif [ -f "$HOME/.kube/config" ]; then
  cp "$HOME/.kube/config" "$TMPKUBE"
elif [ -f "/etc/kubernetes/admin.conf" ]; then
  cp "/etc/kubernetes/admin.conf" "$TMPKUBE"
elif [ -f "/etc/rancher/k3s/k3s.yaml" ]; then
  cp "/etc/rancher/k3s/k3s.yaml" "$TMPKUBE"
elif [ -f "/etc/rancher/rke2/rke2.yaml" ]; then
  cp "/etc/rancher/rke2/rke2.yaml" "$TMPKUBE"
else
  echo -e "${RED}ERROR: Cannot find kubeconfig.${RESET}"; exit 1
fi
chmod 600 "$TMPKUBE"
export KUBECONFIG="$TMPKUBE"
rm -rf "${HOME}/.kube/cache" "${HOME}/.kube/http-cache" 2>/dev/null || true

kubectl cluster-info --request-timeout=15s &>/dev/null || { echo -e "${RED}ERROR: Cannot reach cluster.${RESET}"; exit 1; }

CLUSTER_TYPE="generic"
if kubectl get nodes -o jsonpath='{.items[*].metadata.labels}' 2>/dev/null | grep -q "microk8s"; then CLUSTER_TYPE="microk8s"
elif kubectl get nodes -o jsonpath='{.items[*].metadata.labels}' 2>/dev/null | grep -q "k3s"; then CLUSTER_TYPE="k3s"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "aws"; then CLUSTER_TYPE="eks"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "azure"; then CLUSTER_TYPE="aks"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "gce\|gke"; then CLUSTER_TYPE="gke"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "digitalocean"; then CLUSTER_TYPE="doks"
fi

if [ "$CLUSTER_TYPE" = "microk8s" ]; then
  command -v microk8s &>/dev/null && { microk8s enable hostpath-storage 2>/dev/null || true; microk8s enable dns 2>/dev/null || true; }
fi

if [ -n "$STORAGE_CLASS" ]; then :; else
  STORAGE_CLASS=$(kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null | awk '{print $1}')
  [ -z "$STORAGE_CLASS" ] && STORAGE_CLASS=$(kubectl get storageclass -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  [ -z "$STORAGE_CLASS" ] && case "$CLUSTER_TYPE" in
    microk8s) STORAGE_CLASS="microk8s-hostpath" ;;
    k3s) STORAGE_CLASS="local-path" ;;
    eks) STORAGE_CLASS="gp2" ;;
    gke) STORAGE_CLASS="standard" ;;
    aks) STORAGE_CLASS="default" ;;
    doks) STORAGE_CLASS="do-block-storage" ;;
    *) STORAGE_CLASS="" ;;
  esac
fi

SERVICE_TYPE="${SERVICE_TYPE:-}"
if [ -z "$SERVICE_TYPE" ]; then
  case "$CLUSTER_TYPE" in eks|gke|aks|doks) SERVICE_TYPE="LoadBalancer" ;; *) SERVICE_TYPE="NodePort" ;; esac
fi

AGENT_TOKEN="${AGENT_TOKEN:-$(openssl rand -hex 32)}"
BACKEND_REPO="${BACKEND_IMAGE_REPO:-${IMAGE_REPO:-docker.io/piyushman/kubeeasy-api}}"
FRONTEND_REPO="${FRONTEND_IMAGE_REPO:-docker.io/piyushman/kubeeasy-ui}"
BACKEND_TAG="${BACKEND_IMAGE_TAG:-${IMAGE_TAG:-latest}}"
FRONTEND_TAG="${FRONTEND_IMAGE_TAG:-${IMAGE_TAG:-latest}}"

SC_SET=()
[ -n "$STORAGE_CLASS" ] && SC_SET=(--set "qdrant.storageClassName=$STORAGE_CLASS")

echo -e "${BOLD}Installing $RELEASE with Helm (namespace $NS)…${RESET}"
helm upgrade --install "$RELEASE" "$CHART_DIR" \
  --namespace "$NS" --create-namespace \
  "${GROQ_SET[@]}" \
  --set "agentToken=$AGENT_TOKEN" \
  --set "backend.image.repository=$BACKEND_REPO" \
  --set "backend.image.tag=$BACKEND_TAG" \
  --set "frontend.image.repository=$FRONTEND_REPO" \
  --set "frontend.image.tag=$FRONTEND_TAG" \
  --set "frontend.serviceType=$SERVICE_TYPE" \
  "${SC_SET[@]}" \
  --wait --timeout=600s

BACK_DEP=$(kubectl get deploy -n "$NS" -l "app.kubernetes.io/instance=$RELEASE,app.kubernetes.io/component=backend" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
FE_DEP=$(kubectl get deploy -n "$NS" -l "app.kubernetes.io/instance=$RELEASE,app.kubernetes.io/component=frontend" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -n "$BACK_DEP" ] && kubectl rollout status "deployment/$BACK_DEP" -n "$NS" --timeout=300s
[ -n "$FE_DEP" ] && kubectl rollout status "deployment/$FE_DEP" -n "$NS" --timeout=180s

UI_SVC=$(kubectl get svc -n "$NS" -l "app.kubernetes.io/instance=$RELEASE,app.kubernetes.io/component=frontend" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$UI_SVC" ] && UI_SVC=$(kubectl get svc -n "$NS" -o name 2>/dev/null | grep frontend | head -1 | sed 's|service/||')

echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✅ KubeEasy installed${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo ""
if [ -z "$UI_SVC" ]; then
  echo -e "${BOLD}UI service:${RESET}  kubectl get svc -n $NS -l app.kubernetes.io/component=frontend"
elif [ "$SERVICE_TYPE" = "LoadBalancer" ]; then
  echo -e "${BOLD}UI (wait for EXTERNAL-IP):${RESET}  kubectl get svc $UI_SVC -n $NS -w"
else
  NP=$(kubectl get svc "$UI_SVC" -n "$NS" -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
  NI=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null | awk '{print $1}')
  [ -z "$NI" ] && NI=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null | awk '{print $1}')
  [ -n "$NP" ] && [ -n "$NI" ] && echo -e "${BOLD}UI URL:${RESET}  http://${NI}:${NP}"
fi
echo -e "${BOLD}Agent token:${RESET}  ${CYAN}${AGENT_TOKEN}${RESET}"
echo ""
echo -e "  ${YELLOW}helm get notes $RELEASE -n $NS${RESET}  (connection helpers)"
echo ""

{
  echo "AGENT_TOKEN=${AGENT_TOKEN}"
  echo "NAMESPACE=${NS}"
  echo "RELEASE=${RELEASE}"
} > "$REPO_ROOT/.agent-connection.txt"
echo -e "  Saved ${BOLD}.agent-connection.txt${RESET} (repo root)"
echo ""
