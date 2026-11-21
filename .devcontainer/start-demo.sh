#!/usr/bin/env bash
# .devcontainer/start-demo.sh
# Levanta PostgreSQL + inicializador + dashboard (sin agente ni llama-server).
# Idempotente: se ejecuta en cada arranque del codespace.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f compose.demo.yaml"

echo "==> Levantando stack de demo (postgres + migrator + dashboard)..."
$COMPOSE up -d --build

echo "==> Esperando readiness del dashboard..."
for i in $(seq 1 60); do
    if curl -fsS http://localhost:8501/_stcore/health >/dev/null 2>&1; then
        echo "==> Dashboard listo en el puerto 8501 (pestaña PORTS)."
        exit 0
    fi
    sleep 3
done

echo "AVISO: el dashboard no respondió en 180 s. Revisar: $COMPOSE logs dashboard" >&2
exit 1
