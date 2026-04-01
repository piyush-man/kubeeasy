#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/parameters.conf"
while [[ $# -gt 0 ]]; do
    case $1 in --namespace) namespace="$2"; shift 2 ;; --name) name="$2"; shift 2 ;; *) shift ;; esac
done
[[ -z "$namespace" || -z "$name" ]] && { echo "Error: missing namespace or name"; exit 1; }
$KUBECTL_PATH delete deployment "$name" -n "$namespace" --ignore-not-found >/dev/null
echo "Application stopped successfully"
