#!/usr/bin/env bash
# .devcontainer/build-demo.sh
# Pre-descarga las imagenes de la demo desde GHCR.
#
# Los prebuilds de Codespaces ejecutan `devcontainer up --skip-post-create`, que
# omite onCreateCommand/postCreateCommand/postStartCommand. Por eso este script
# NO es la via principal: `start-demo.sh` hace `pull` en cada arranque.
# Se mantiene para ejecucion manual y como documentacion del flujo.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f compose.demo.yaml"

for i in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: el daemon de Docker no respondio en 120 s." >&2
    exit 1
fi

echo "==> Descargando imagenes publicadas en GHCR..."
$COMPOSE pull || echo "AVISO: pull fallo; se construira localmente al arrancar."

echo "==> Listo."
