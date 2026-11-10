# Resumen de sesión 0023 — llama-server con GPU/CDI y E2E agente real con LLM

**Hito:** Resolución del pendiente G1 del paquete E4: levantar `llama-server`
(GPU/CDI) y ejecutar el E2E del agente real con LLM (CrewAI + llama.cpp).
**Rama:** `main` (verificación; sin cambios de código).
**Fecha:** 2026-11-09

---

## Problema resuelto

El pendiente documentado en `docs/TFG/E4-ENTREGABLE-PREPARADO.md` (§B.2 y
tabla G1) indicaba:

- `llama-server :8080` no accesible (contenedor no levantado; exige GPU/CDI).
- E2E agente real con LLM (CrewAI+llama) NO ejecutado.

La evidencia disponible era solo indirecta (tests de integración con
`test_crewai_real.py`). Este hito levanta el servicio con GPU real y ejecuta
el agente de punta a punta contra el LLM local.

---

## Qué se hizo

### 1. Diagnóstico del entorno

- Podman corre en la VM WSL2 `podman-fran` (rootless, conexión SSH
  `ssh://user@127.0.0.1:65508`).
- GPU **NVIDIA RTX 2000 Ada Generation Laptop** (8 GiB VRAM) visible dentro de
  la VM (`nvidia-smi` OK, driver 597.01, CUDA 13.2).
- CDI configurado: `/etc/cdi/nvidia.yaml` y `/var/run/cdi/nvidia.yaml`
  (device `nvidia.com/gpu=all`, hook `nvidia-cdi-hook`).
- Modelo GGUF presente en el worktree raíz:
  `models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` (4.92 GB).
- Imagen `ghcr.io/ggml-org/llama.cpp:server-cuda` ya descargada (4.36 GB).

### 2. Levantamiento de llama-server (GPU/CDI)

```powershell
podman compose up -d llama-server
```

- Contenedor `argentgob-mvp-llama-server-1` → **Up (healthy)** en ~45 s.
- Health: `GET http://localhost:8080/health` → `{"status":"ok"}`.
- GPU en uso: **5655 MiB / 8188 MiB VRAM** ocupados por el modelo
  (verificado con `nvidia-smi` dentro de la VM).
- Logs: `model loaded`, `listening on http://0.0.0.0:8080`.
- Smoke test de la API: `POST /v1/chat/completions` → HTTP 200, respuesta
  `OK` (prompt 42 tokens, ~151 tok/s de prompt, ~26 tok/s de generación).

### 3. E2E agente real con LLM

```powershell
podman run --rm --network argentgob-mvp_default `
  -v "${PWD}:/app:Z" -w /app `
  -e OPENAI_API_BASE=http://llama-server:8080/v1 `
  -e OPENAI_API_KEY=local-no-key-required `
  -e OPENAI_MODEL_NAME=llama-3.1-8b-instruct `
  -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO `
  -e "DATABASE_URL=postgresql+psycopg://argentgob_app:***@postgres:5432/argentgob" `
  localhost/argentgob-mvp-agent:latest `
  python -m argentgob.agent.main AAPL --profile analyst
```

Resultado:

- **`agent_run_complete status=SUCCESS`** (run_id `0c98fd60-...`).
- Informe fundacional generado por el LLM real con datos de mercado:
  precio AAPL $350.69, variación +2.33%, volumen 42.7M.
- **Gobernanza activa end-to-end** (persistida en PostgreSQL real):
  - `news` → **HITL** (`PREDICTED_MEDIUM_RISK`) — bloqueado sin ejecutar
    (INV-01/02: la tool no consulta el provider).
  - `stock_price` → **PASS** (`ALLOWED`) — ejecutó y devolvió precio.
  - `crypto_price` → **PASS** (`ALLOWED`) — error externo de CoinGecko
    (formato de string), no de gobernanza.
- Decisiones persistidas: `governance_decisions` con 349 filas totales
  (incluye las nuevas del E2E).

### 4. Verificación del stack completo

| Servicio | Estado |
|---|---|
| llama-server :8080 | HTTP 200 `{"status":"ok"}` |
| dashboard :8501 | HTTP 200 (`_stcore/health`) |
| postgres | `pg_isready` OK (healthy) |

---

## Evidencia

- `podman ps`: `argentgob-mvp-llama-server-1 Up (healthy) 0.0.0.0:8080->8080/tcp`.
- `nvidia-smi` (VM): 5655 MiB VRAM en uso por llama-server.
- Log del agente: `agent_run_complete ... status=SUCCESS`.
- DB: 349 decisiones en `governance_decisions` (HITL/PASS del E2E incluidos).

---

## Documentación actualizada

- `docs/TFG/E4-ENTREGABLE-PREPARADO.md`:
  - §B.2: llama-server → [VERIFICADO] (HTTP 200, GPU/CDI, 5655 MiB VRAM).
  - §B.2: E2E agente real → [VERIFICADO] (SUCCESS, gobernanza activa,
    persistencia en PG real).
  - Tabla G1: brecha **RESUELTA** (con el comando y el resultado).

---

## Pendientes restantes (no bloqueantes)

- G2: video demo 3–5 min.
- G3: enlace público repo/demo.
- G4: correo del autor en portada.
- G5: paginación definitiva del índice.
- G6: precios de costos al día del cierre.
- G7: citas sin referencia / referencias sin cita (APA).
- G8: validación con usuarios reales (opcional).
- G9: documentar decisiones históricas sin `payload_digest`/`execution_result`.