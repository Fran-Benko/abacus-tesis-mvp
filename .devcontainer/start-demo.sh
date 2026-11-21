#!/usr/bin/env bash
# .devcontainer/start-demo.sh
# Levanta PostgreSQL + inicializador + dashboard (sin agente ni llama-server).
#
# Se ejecuta en `postStartCommand`: en cada arranque del codespace.
# NO construye imagenes: eso ocurre en `onCreateCommand` (build-demo.sh), de
# modo que el prebuild las cachea y el arranque es rapido.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f compose.demo.yaml"

# Espera a que el daemon de Docker-in-Docker acepte conexiones.
for i in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: el daemon de Docker no respondio en 60 s." >&2
    exit 1
fi

# Si el prebuild no cacheo las imagenes, construirlas ahora (una sola vez).
if ! docker image inspect argentgob-demo-dashboard >/dev/null 2>&1 \
   || ! docker image inspect argentgob-demo-migrator >/dev/null 2>&1; then
    echo "==> Imagenes ausentes; construyendo (~5-10 min)..."
    $COMPOSE build
fi

echo "==> Levantando servicios..."
$COMPOSE up -d

echo "==> Esperando readiness del dashboard..."
for i in $(seq 1 60); do
    if curl -fsS http://localhost:8501/_stcore/health >/dev/null 2>&1; then
        echo "==> Dashboard listo en el puerto 8501 (pestana PORTS)."
        exit 0
    fi
    sleep 3
done

echo "AVISO: el dashboard no respondio en 180 s. Revisar: $COMPOSE logs dashboard" >&2
exit 1
