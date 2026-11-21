#!/usr/bin/env bash
# .devcontainer/setup-demo.sh
# Prepara el codespace para la demo: .env, secretos y artefactos.
# NO descarga el GGUF (~4.9 GB) ni construye llama-server: la demo pública
# corre sin agente ni LLM local (ver compose.demo.yaml).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Preparando entorno de demo (sin agente ni LLM)..."

if [ ! -f .env ]; then
    cp .env.example .env
    echo "  .env creado desde .env.example"
fi

mkdir -p .secrets
chmod 700 .secrets 2>/dev/null || true

# Contraseña administrativa de PostgreSQL (el resto lo genera init_secrets.py).
if [ ! -f .secrets/postgres_password ]; then
    python -c "import secrets;print(secrets.token_urlsafe(18),end='')" > .secrets/postgres_password
    chmod 600 .secrets/postgres_password 2>/dev/null || true
    echo "  .secrets/postgres_password creado"
fi

python scripts/init_secrets.py

echo "==> Entorno listo. El dashboard se levanta en el puerto 8501."
