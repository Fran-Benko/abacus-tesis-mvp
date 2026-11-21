#!/usr/bin/env bash
# .devcontainer/start-demo.sh
# Levanta PostgreSQL + inicializador + dashboard (sin agente ni llama-server).
#
# Se ejecuta en `postStartCommand`, en cada arranque del codespace. Descarga las
# imagenes publicadas en GHCR (rapido) y solo construye si el pull falla.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f compose.demo.yaml"

# El daemon de Docker-in-Docker tarda en levantar tras crear el contenedor.
for i in $(seq 1 45); do
    if docker info >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: el daemon de Docker no respondio en 90 s." >&2
    exit 1
fi

echo "==> Descargando imagenes de la demo..."
if ! $COMPOSE pull; then
    echo "AVISO: pull fallo (imagen no publicada aun); construyendo localmente..."
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
