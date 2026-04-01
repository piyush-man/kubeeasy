#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/parameters.conf"

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

[[ -z "$namespace" || -z "$name" ]] && { echo "Error: --namespace and --name required"; exit 1; }

VALUES_FILE="/data/${name}-${namespace}.yaml"
[[ ! -f "$VALUES_FILE" ]] && { echo "Error: no saved values for $name/$namespace"; exit 1; }

temp_values=$(mktemp)
{
    echo "volumes:"
    for i in "${!mounts[@]}"; do
        IFS=':' read -r mp pp <<< "${mounts[$i]}"
        echo "  - name: shared$((i+1))"; echo "    nfs:"; echo "      server: $nfsip"; echo "      path: $mp"
    done
    echo "volumeMounts:"
    for i in "${!mounts[@]}"; do
        pp="${mounts[$i]#*:}"
        echo "  - name: shared$((i+1))"; echo "    mountPath: $pp"; echo "    subPath: Mount$i"
    done
} > "$temp_values"

cmd="$HELM_PATH upgrade $name.$namespace $MAIN_PATH/appconfig/. -f $VALUES_FILE -f $temp_values"
[[ -n "$image" ]]       && cmd+=" --set image.repository=\"$image\""
[[ -n "$version" ]]     && cmd+=" --set image.tag=\"$version\""
[[ -n "$port" ]]        && cmd+=" --set service.port=\"$port\" --set livenessProbe.tcpSocket.port=\"$port\" --set readinessProbe.tcpSocket.port=\"$port\""
[[ -n "$autoscaling" ]] && cmd+=" --set autoscaling.enabled=\"$autoscaling\""
[[ -n "$minreplicas" ]] && cmd+=" --set autoscaling.minReplicas=\"$minreplicas\""
[[ -n "$maxreplicas" ]] && cmd+=" --set autoscaling.maxReplicas=\"$maxreplicas\""
[[ -n "$maxcpu" ]]      && cmd+=" --set resources.limits.cpu=\"$maxcpu\""
[[ -n "$maxmem" ]]      && cmd+=" --set resources.limits.memory=\"$maxmem\""
[[ -n "$mincpu" ]]      && cmd+=" --set resources.requests.cpu=\"$mincpu\""
[[ -n "$minmem" ]]      && cmd+=" --set resources.requests.memory=\"$minmem\""
[[ -n "$percentage" ]]  && cmd+=" --set autoscaling.targetCPUUtilizationPercentage=\"$percentage\""
[[ -n "$replicas" ]]    && cmd+=" --set replicaCount=\"$replicas\""

if eval "$cmd" >/dev/null; then
    $HELM_PATH get values "$name.$namespace" -o yaml > "$VALUES_FILE"
    echo "Success: Updated $name"
else
    echo "Error: Update failed for $name"; exit 1
fi
rm -f "$temp_values"
