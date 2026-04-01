#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/parameters.conf"
while [[ $# -gt 0 ]]; do
    case $1 in --namespace) namespace="$2"; shift 2 ;; --name) name="$2"; shift 2 ;; *) shift ;; esac
done
[[ -z "$namespace" || -z "$name" ]] && { echo "Error: missing namespace or name"; exit 1; }

$HELM_PATH uninstall "$name.$namespace" >/dev/null 2>&1 || true
$KUBECTL_PATH delete deployment,service,ingress -l "app.kubernetes.io/name=$name" -n "$namespace" --ignore-not-found >/dev/null
rm -f "/data/${name}-${namespace}.yaml"

# Remove from index
[[ -f "$INDEX_FILE" ]] && {
    tmp=$(mktemp)
    jq --arg ns "$namespace" --arg n "$name" 'del(.[$ns][$n])' "$INDEX_FILE" > "$tmp" && mv "$tmp" "$INDEX_FILE"
    # Remove namespace from index if empty
    remaining=$(jq --arg ns "$namespace" '.[$ns]|length' "$INDEX_FILE")
    [[ "$remaining" == "0" ]] && {
        tmp=$(mktemp)
        jq --arg ns "$namespace" 'del(.[$ns])' "$INDEX_FILE" > "$tmp" && mv "$tmp" "$INDEX_FILE"
    }
}
echo "Application removed successfully"
