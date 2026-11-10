#!/usr/bin/env bash
# scripts/verify_stack.sh
# Verifica que los servicios del stack esten arriba y saludables.

set -uo pipefail

echo "==> Verificando estado del stack ArgentGob-Mesh..."

fallos=0

check_http() {
    local nombre="$1"
    local url="$2"
    if curl -fsS "$url" >/dev/null 2>&1; then
        echo "  [OK]    ${nombre} responde en ${url}"
    else
        echo "  [FALLO] ${nombre} no responde en ${url}"
        fallos=$((fallos + 1))
    fi
}

check_agent_ready() {
    # AV-02: la API interna no publica puerto; se consulta desde su contenedor.
    if ! command -v podman >/dev/null 2>&1; then
        echo "  [SKIP]  podman no disponible; se omite readiness del agente"
        return
    fi
    local ready
    ready=$(podman exec argentgob-mvp-agent-1 python -c \
        "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8090/ready'))['ready'])" \
        2>/dev/null || echo "error")
    if [ "$ready" = "True" ]; then
        echo "  [OK]    agent /ready (esquema en head y catalogo sembrado)"
    else
        echo "  [FALLO] agent /ready no listo (${ready})"
        fallos=$((fallos + 1))
    fi
}

check_migrator() {
    if ! command -v podman >/dev/null 2>&1; then
        return
    fi
    local code
    code=$(podman inspect --format '{{.State.ExitCode}}' argentgob-mvp-migrator-1 2>/dev/null || echo "missing")
    if [ "$code" = "0" ]; then
        echo "  [OK]    inicializador (migrator) exit 0"
    else
        echo "  [FALLO] inicializador (migrator) exit=${code}"
        fallos=$((fallos + 1))
    fi
}

check_pg() {
    if command -v podman >/dev/null 2>&1; then
        if podman compose exec -T postgres pg_isready -U argentgob_app -d argentgob >/dev/null 2>&1; then
            echo "  [OK]    PostgreSQL acepta conexiones"
        else
            echo "  [FALLO] PostgreSQL no esta listo"
            fallos=$((fallos + 1))
        fi
    else
        echo "  [SKIP]  podman no disponible; se omite chequeo de PostgreSQL"
    fi
}

check_http "llama-server" "http://localhost:8080/health"
check_http "dashboard" "http://localhost:8501/_stcore/health"
check_pg
check_migrator
check_agent_ready

if [ "$fallos" -eq 0 ]; then
    echo "==> Todos los servicios verificados correctamente."
    exit 0
else
    echo "==> ${fallos} servicio(s) con problemas. Revisa 'podman compose logs'."
    exit 1
fi
