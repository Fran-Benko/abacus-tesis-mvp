#!/usr/bin/env bash
# scripts/codespace_status.sh
# Diagnostico del stack de demo dentro de un codespace.
#
# Responde tres preguntas:
#   1. Estan los contenedores arriba?
#   2. Responde Streamlit en 8501?
#   3. El puerto es publico (sin login) o privado?
#
# Uso (dentro del codespace):
#   bash scripts/codespace_status.sh
set -uo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f compose.demo.yaml"
fallos=0

echo "==> 1. Contenedores"
if ! docker info >/dev/null 2>&1; then
    echo "  [FALLO] el daemon de Docker no responde"
    exit 1
fi
$COMPOSE ps || true

echo
echo "==> 2. Salud de Streamlit (localhost:8501)"
if curl -fsS http://localhost:8501/_stcore/health >/dev/null 2>&1; then
    echo "  [OK] Streamlit responde"
else
    echo "  [FALLO] Streamlit no responde; revisar: $COMPOSE logs --tail 60 dashboard"
    fallos=$((fallos + 1))
fi

echo
echo "==> 3. Visibilidad del puerto reenviado"
if [ -n "${CODESPACE_NAME:-}" ]; then
    URL="https://${CODESPACE_NAME}-8501.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
    echo "  URL: $URL"
    BODY="$(curl -fsS --max-time 20 "$URL/_stcore/health" 2>/dev/null || true)"
    if [ -z "$BODY" ]; then
        echo "  [FALLO] la URL publica no responde"
        fallos=$((fallos + 1))
    elif printf '%s' "$BODY" | grep -q 'pf-signin\|Connecting to the forwarded port'; then
        echo "  [PRIVADO] el puerto exige login."
        echo "            Pesta?a PORTS -> puerto 8501 -> clic derecho ->"
        echo "            Port Visibility -> Public."
        echo "            O por CLI: gh codespace ports visibility 8501:public -c $CODESPACE_NAME"
        fallos=$((fallos + 1))
    else
        echo "  [OK] el puerto es publico y responde sin login"
    fi
else
    echo "  [SKIP] no es un codespace (CODESPACE_NAME ausente)"
fi

echo
if [ "$fallos" -eq 0 ]; then
    echo "==> Todo listo para la demo."
    exit 0
fi
echo "==> $fallos problema(s) detectado(s)."
exit 1
