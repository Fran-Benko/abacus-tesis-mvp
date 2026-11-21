#!/usr/bin/env bash
# .devcontainer/start-demo.sh
# Levanta PostgreSQL + inicializador + dashboard (sin agente ni llama-server).
#
# Idempotente y apto para `postStartCommand`: solo construye la primera vez.
# Reconstruir en cada arranque agotaba el tiempo del hook y la cuota del
# codespace, porque cada imagen pesa ~1.6 GB.
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

# Construir solo si falta alguna imagen del proyecto.
if ! docker image inspect argentgob-demo-dashboard >/dev/null 2>&1 \
   || ! docker image inspect argentgob-demo-migrator >/dev/null 2>&1; then
    echo "==> Construyendo imagenes (primera vez, ~5-10 min)..."
    $COMPOSE build
else
    echo "==> Imagenes presentes; se omite el build."
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
