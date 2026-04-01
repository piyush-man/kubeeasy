#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/parameters.conf"
while [[ $# -gt 0 ]]; do
    case $1 in --namespace) namespace="$2"; shift 2 ;; --name) name="$2"; shift 2 ;; *) shift ;; esac
done
[[ -z "$namespace" || -z "$name" ]] && { echo "Error: missing namespace or name"; exit 1; }
VALUES_FILE="/data/${name}-${namespace}.yaml"
[[ ! -f "$VALUES_FILE" ]] && { echo "Error: no values file for $name/$namespace"; exit 1; }
$HELM_PATH upgrade "$name.$namespace" "$MAIN_PATH/appconfig/." -f "$VALUES_FILE" >/dev/null
echo "Application started successfully"
