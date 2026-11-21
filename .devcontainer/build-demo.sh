#!/usr/bin/env bash
# .devcontainer/build-demo.sh
# Construye las imagenes de la demo. Se ejecuta en `onCreateCommand`, que SI
# corre durante el prebuild del codespace: asi las imagenes quedan cacheadas y
# el arranque posterior no gasta cuota construyendo 3 GB.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f compose.demo.yaml"

# El daemon de Docker-in-Docker tarda en levantar tras crear el contenedor.
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

echo "==> Construyendo imagenes de la demo (se cachean en el prebuild)..."
$COMPOSE build

echo "==> Imagenes listas."
