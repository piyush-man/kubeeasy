#!/bin/bash
# Deploy a new application via Helm. Uses in-cluster kubectl/helm — no kubeconfig.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/parameters.conf"
source "$SCRIPT_DIR/defaults.conf"

while [[ $# -gt 0 ]]; do
    case $1 in
        --name)        name="$2";       shift 2 ;;
        --namespace)   namespace="$2";  shift 2 ;;
        --replicas)    replicas="$2";   shift 2 ;;
        --image)       image="$2";      shift 2 ;;
        --version)     version="$2";    shift 2 ;;
        --port)        port="$2";       shift 2 ;;
        --autoscaling) autoscaling="$2";shift 2 ;;
        --mincpu)      mincpu="$2";     shift 2 ;;
        --maxcpu)      maxcpu="$2";     shift 2 ;;
        --minmem)      minmem="$2";     shift 2 ;;
        --maxmem)      maxmem="$2";     shift 2 ;;
        --percentage)  percentage="$2"; shift 2 ;;
        --minreplicas) minreplicas="$2";shift 2 ;;
        --maxreplicas) maxreplicas="$2";shift 2 ;;
        --nfsip)       nfsip="$2";      shift 2 ;;
        --mount*)      mounts+=("${2:-}:${3:-}"); shift 3 ;;
        *) shift ;;
    esac
done

[[ -z "$namespace" || -z "$name" || -z "$image" || -z "$port" ]] && {
    echo "Error: --namespace, --name, --image, --port are required."; exit 1; }

[[ "$name" == "$namespace" ]] && {
    echo "Error: name and namespace must differ."; exit 1; }

$KUBECTL_PATH get deployment "$name" -n "$namespace" >/dev/null 2>&1 && {
    echo "Error: '$name' already exists in '$namespace'."; exit 1; }

# Create namespace if needed
$KUBECTL_PATH get namespace "$namespace" >/dev/null 2>&1 || \
    $KUBECTL_PATH create ns "$namespace" >/dev/null

# Build NFS volume values
temp_values=$(mktemp)
{
    echo "volumes:"
    for i in "${!mounts[@]}"; do
        IFS=':' read -r mp pp <<< "${mounts[$i]}"
        echo "  - name: shared$((i+1))"
        echo "    nfs:"
        echo "      server: $nfsip"
        echo "      path: $mp"
    done
    echo "volumeMounts:"
    for i in "${!mounts[@]}"; do
        pp="${mounts[$i]#*:}"
        echo "  - name: shared$((i+1))"
        echo "    mountPath: $pp"
        echo "    subPath: Mount$i"
    done
} > "$temp_values"

if $HELM_PATH install "$name.$namespace" "$MAIN_PATH/appconfig/." \
    -f "$MAIN_PATH/appconfig/default.yaml" \
    -f "$temp_values" \
    --set nameOverride="$name" \
    --set fullnameOverride="$name" \
    --set namespace="$namespace" \
    --set image.repository="$image" \
    --set image.tag="$version" \
    --set service.port="$port" \
    --set livenessProbe.tcpSocket.port="$port" \
    --set readinessProbe.tcpSocket.port="$port" \
    --set autoscaling.enabled="$autoscaling" \
    --set autoscaling.minReplicas="$minreplicas" \
    --set autoscaling.maxReplicas="$maxreplicas" \
    --set resources.limits.cpu="$maxcpu" \
    --set resources.limits.memory="$maxmem" \
    --set resources.requests.cpu="$mincpu" \
    --set resources.requests.memory="$minmem" \
    --set autoscaling.targetCPUUtilizationPercentage="$percentage" \
    --set replicaCount="$replicas" >/dev/null; then

    # Save helm values
    $HELM_PATH get values "$name.$namespace" -o yaml > "/data/${name}-${namespace}.yaml"

    # Update index
    [[ ! -f "$INDEX_FILE" ]] && echo "{}" > "$INDEX_FILE"
    tmp=$(mktemp)
    jq --arg ns "$namespace" --arg n "$name" --arg port "$port" \
       --arg image "${image}:${version}" \
       '.[$ns][$n] = {"port":$port,"name":$n,"image":$image}' \
       "$INDEX_FILE" > "$tmp" && mv "$tmp" "$INDEX_FILE"

    echo "Success: Deployed $name in $namespace"
else
    echo "Error: Helm install failed for $name"
    exit 1
fi
rm -f "$temp_values"
