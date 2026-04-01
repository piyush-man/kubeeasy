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
echo -e "  Manager — Universal Kubernetes Installer${RESET}"
echo ""

# ── Prerequisite check ─────────────────────────────────────────────────────
for cmd in kubectl helm openssl; do
  command -v "$cmd" &>/dev/null || { echo -e "${RED}ERROR: '$cmd' not found in PATH.${RESET}"; exit 1; }
done

# ── Load .env ──────────────────────────────────────────────────────────────
ENV_FILE="$(dirname "$0")/.env"
[ -f "$ENV_FILE" ] || { echo -e "${RED}ERROR: .env not found. Run: cp .env.example .env${RESET}"; exit 1; }
set -a; source "$ENV_FILE"; set +a
[ -n "$GROQ_API_KEY" ] || { echo -e "${RED}ERROR: GROQ_API_KEY not set in .env${RESET}"; exit 1; }

# ── Resolve kubeconfig ────────────────────────────────────────────────────
echo -e "${BOLD}Detecting Kubernetes cluster…${RESET}"

TMPKUBE=$(mktemp /tmp/kubeconfig.XXXXXX)
trap "rm -f $TMPKUBE" EXIT

if [ -n "$KUBECONFIG" ] && [ -f "$KUBECONFIG" ]; then
  cp "$KUBECONFIG" "$TMPKUBE"
  echo -e "  ✓ Using KUBECONFIG env: $KUBECONFIG"
elif command -v microk8s &>/dev/null; then
  microk8s config > "$TMPKUBE"
  echo -e "  ✓ Detected MicroK8s — exporting config"
elif [ -f "$HOME/.kube/config" ]; then
  cp "$HOME/.kube/config" "$TMPKUBE"
  echo -e "  ✓ Using ~/.kube/config"
elif [ -f "/etc/kubernetes/admin.conf" ]; then
  cp "/etc/kubernetes/admin.conf" "$TMPKUBE"
  echo -e "  ✓ Using /etc/kubernetes/admin.conf (kubeadm)"
elif [ -f "/etc/rancher/k3s/k3s.yaml" ]; then
  cp "/etc/rancher/k3s/k3s.yaml" "$TMPKUBE"
  echo -e "  ✓ Detected K3s — using k3s.yaml"
elif [ -f "/etc/rancher/rke2/rke2.yaml" ]; then
  cp "/etc/rancher/rke2/rke2.yaml" "$TMPKUBE"
  echo -e "  ✓ Detected RKE2"
else
  echo -e "${RED}ERROR: Cannot find kubeconfig.${RESET}"
  echo "  Set KUBECONFIG env var or place config at ~/.kube/config"
  exit 1
fi

chmod 600 "$TMPKUBE"
export KUBECONFIG="$TMPKUBE"
rm -rf "${HOME}/.kube/cache" "${HOME}/.kube/http-cache" 2>/dev/null || true

echo -e "${BOLD}Verifying cluster connectivity…${RESET}"
kubectl cluster-info --request-timeout=15s 2>/dev/null || {
  echo -e "${RED}ERROR: Cannot reach cluster. Check network and kubeconfig.${RESET}"
  exit 1
}

# ── Detect cluster type & storage class ───────────────────────────────────
echo -e "${BOLD}Detecting cluster environment…${RESET}"

CLUSTER_TYPE="generic"
if kubectl get nodes -o jsonpath='{.items[*].metadata.labels}' 2>/dev/null | grep -q "microk8s"; then
  CLUSTER_TYPE="microk8s"
elif kubectl get nodes -o jsonpath='{.items[*].metadata.labels}' 2>/dev/null | grep -q "k3s"; then
  CLUSTER_TYPE="k3s"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "aws"; then
  CLUSTER_TYPE="eks"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "azure"; then
  CLUSTER_TYPE="aks"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "gce\|gke"; then
  CLUSTER_TYPE="gke"
elif kubectl get nodes -o jsonpath='{.items[*].spec.providerID}' 2>/dev/null | grep -qi "digitalocean"; then
  CLUSTER_TYPE="doks"
fi
echo -e "  ✓ Cluster type: ${CYAN}${CLUSTER_TYPE}${RESET}"

# Auto-detect or use provided storage class
if [ -n "$STORAGE_CLASS" ]; then
  echo -e "  ✓ Storage class (from .env): ${CYAN}${STORAGE_CLASS}${RESET}"
else
  # Try to detect available storage class
  STORAGE_CLASS=$(kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null | awk '{print $1}')
  if [ -z "$STORAGE_CLASS" ]; then
    STORAGE_CLASS=$(kubectl get storageclass -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  fi
  if [ -z "$STORAGE_CLASS" ]; then
    # Cluster-specific fallbacks
    case "$CLUSTER_TYPE" in
      microk8s) STORAGE_CLASS="microk8s-hostpath" ;;
      k3s)      STORAGE_CLASS="local-path" ;;
      eks)      STORAGE_CLASS="gp2" ;;
      gke)      STORAGE_CLASS="standard" ;;
      aks)      STORAGE_CLASS="default" ;;
      doks)     STORAGE_CLASS="do-block-storage" ;;
      *)        STORAGE_CLASS="standard" ;;
    esac
    echo -e "  ${YELLOW}⚠ No default storage class found. Using: ${STORAGE_CLASS}${RESET}"
    echo -e "  ${YELLOW}  Set STORAGE_CLASS= in .env to override.${RESET}"
  else
    echo -e "  ✓ Auto-detected storage class: ${CYAN}${STORAGE_CLASS}${RESET}"
  fi
fi

# ── Enable MicroK8s addons if needed ──────────────────────────────────────
if [ "$CLUSTER_TYPE" = "microk8s" ]; then
  echo -e "${BOLD}Enabling MicroK8s addons…${RESET}"
  microk8s enable hostpath-storage 2>/dev/null || true
  microk8s enable dns 2>/dev/null || true
fi

# ── Detect service type ───────────────────────────────────────────────────
SERVICE_TYPE="${SERVICE_TYPE:-NodePort}"
# Cloud clusters default to LoadBalancer unless overridden
if [ -z "$SERVICE_TYPE" ]; then
  case "$CLUSTER_TYPE" in
    eks|gke|aks|doks) SERVICE_TYPE="LoadBalancer" ;;
    *)                SERVICE_TYPE="NodePort" ;;
  esac
fi
echo -e "  ✓ Service type: ${CYAN}${SERVICE_TYPE}${RESET}"

# ── Generate agent token ───────────────────────────────────────────────────
AGENT_TOKEN=$(openssl rand -hex 32)
IMAGE_REPO="${IMAGE_REPO:-docker.io/piyushman/kubemanager}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# ── Clean pre-existing cluster-scoped resources ───────────────────────────
echo -e "${BOLD}Cleaning existing resources…${RESET}"
kubectl delete clusterrolebinding kubemanager-binding --ignore-not-found 2>/dev/null || true
kubectl delete clusterrole kubemanager-role --ignore-not-found 2>/dev/null || true
kubectl delete serviceaccount kubemanager -n kubemanager --ignore-not-found 2>/dev/null || true

# ── Helm install ──────────────────────────────────────────────────────────
echo -e "${BOLD}Installing via Helm…${RESET}"
helm upgrade --install kubemanager "$(dirname "$0")/helm/kubemanager" \
  --namespace kubemanager --create-namespace \
  --set groqApiKey="$GROQ_API_KEY" \
  --set agentToken="$AGENT_TOKEN" \
  --set image.repository="$IMAGE_REPO" \
  --set image.tag="$IMAGE_TAG" \
  --set qdrant.storageClassName="$STORAGE_CLASS" \
  --set api.serviceType="$SERVICE_TYPE" \
  --wait --timeout=240s

echo -e "${BOLD}Waiting for rollout…${RESET}"
kubectl rollout status deployment/kubemanager-api -n kubemanager --timeout=180s

# ── Determine access URL ──────────────────────────────────────────────────
echo ""
NODE_IP=""
NODE_PORT=""
LB_IP=""

if [ "$SERVICE_TYPE" = "LoadBalancer" ]; then
  echo -e "${BOLD}Waiting for LoadBalancer IP (up to 90s)…${RESET}"
  for i in $(seq 1 18); do
    LB_IP=$(kubectl get svc kubemanager-api -n kubemanager -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
    LB_HOST=$(kubectl get svc kubemanager-api -n kubemanager -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    if [ -n "$LB_IP" ]; then break; fi
    if [ -n "$LB_HOST" ]; then LB_IP="$LB_HOST"; break; fi
    sleep 5
  done
  SVC_PORT=$(kubectl get svc kubemanager-api -n kubemanager -o jsonpath='{.spec.ports[0].port}' 2>/dev/null || echo "8080")
  if [ -n "$LB_IP" ]; then
    WS_URL="ws://${LB_IP}:${SVC_PORT}/ws"
  else
    echo -e "${YELLOW}  LoadBalancer IP not yet assigned. Use kubectl port-forward as fallback.${RESET}"
    WS_URL="ws://localhost:8080/ws"
  fi
else
  NODE_PORT=$(kubectl get svc kubemanager-api -n kubemanager -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
  # Try multiple methods to get node IP
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null | awk '{print $1}')
  if [ -z "$NODE_IP" ]; then
    NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null | awk '{print $1}')
  fi
  WS_URL="ws://${NODE_IP}:${NODE_PORT}/ws"
fi

# ── Print results ─────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✅ KubeManager installed successfully!${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Cluster type:${RESET}  ${CYAN}${CLUSTER_TYPE}${RESET}"
echo -e "  ${BOLD}Storage class:${RESET} ${CYAN}${STORAGE_CLASS}${RESET}"
echo -e "  ${BOLD}Service type:${RESET}  ${CYAN}${SERVICE_TYPE}${RESET}"
echo ""
echo -e "  ${BOLD}Open the UI:${RESET}   client/index.html in your browser"
echo -e "  ${BOLD}WebSocket URL:${RESET} ${CYAN}${WS_URL}${RESET}"
echo -e "  ${BOLD}Token:${RESET}         ${CYAN}${AGENT_TOKEN}${RESET}"
echo ""

# ── Kubectl port-forward tip for cloud clusters ───────────────────────────
if [ "$CLUSTER_TYPE" = "eks" ] || [ "$CLUSTER_TYPE" = "gke" ] || [ "$CLUSTER_TYPE" = "aks" ]; then
  echo -e "  ${YELLOW}Tip: If NodePort is blocked by firewall, use port-forward:${RESET}"
  echo -e "  ${YELLOW}  kubectl port-forward svc/kubemanager-api 8080:8080 -n kubemanager${RESET}"
  echo -e "  ${YELLOW}  Then use: ws://localhost:8080/ws${RESET}"
  echo ""
fi

# ── Save connection info ──────────────────────────────────────────────────
{
  echo "WS_URL=${WS_URL}"
  echo "AGENT_TOKEN=${AGENT_TOKEN}"
  echo "CLUSTER_TYPE=${CLUSTER_TYPE}"
  echo "STORAGE_CLASS=${STORAGE_CLASS}"
} > "$(dirname "$0")/.agent-connection.txt"
echo -e "  Connection info saved to ${BOLD}.agent-connection.txt${RESET}"
echo ""
