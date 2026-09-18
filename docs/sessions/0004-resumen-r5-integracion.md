# Resumen de sesión — R5 implementado (Integración: logs de intentos sanitizados)

Fecha: 2026-09-18
Rama: `agents/continuar-implementacion-hito`
Plan: `mvp-plan-argentgob-v0.3-ampliado.md` (fuente normativa, NO está en el repo; está en `~/Downloads/`)

## Objetivo cumplido

Implementar **R5 — Integración** del track R del plan. R5 quedó **completo**:
logs de intentos de proveedor sanitizados (`news_provider_attempt`) con
correlación (`event_id`, `decision_id`), proveedor, intento, estado y duración,
sin filtrar query, URL completa, headers, cuerpo ni excepción cruda (no-leak),
y sin crear resultados de ejecución por cada retry. Todo determinístico, sin
red ni LLM.

## Estado del repositorio

- Rama de trabajo: `agents/continuar-implementacion-hito` (base `e576db1`).
- `main` → `e576db1` (worktree `plan-implementacion-avancemos`).
- Working tree: 1 modificado + 2 nuevos (ver abajo).

### Configuración SSH (sigue vigente)

```bash
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe'
```

**Usar forward slashes**. Sin esto, `git push`/`git fetch` fallan.

## Baseline de tests

- **86 tests pasan** (baseline 78 → +8 R5) vía:
  `podman run --rm -v "${PWD}:/app" -w /app argentgob-mvp-agent:latest python -m pytest tests/ -q`
- **Ruff NO está instalado** en la imagen del agente.

## Imagen del contenedor

La imagen `argentgob-mvp-agent:latest` fue **rebuildada** con los cambios de
`news_tool.py` (nuevo ID `64b7d3c0cac6`). Verificado que la imagen contiene
`_log_attempt` en `/app/src/argentgob/tools/news_tool.py`.

```bash
podman build -t argentgob-mvp-agent:latest -f containers/agent.Containerfile .
```

## Qué se implementó (R5)

1. **Logs de intentos sanitizados** — `src/argentgob/tools/news_tool.py`:
   - Reemplazó `log_provider_failure` por `_log_attempt` y `_attempt_context`.
   - `_log_attempt` registra `news_provider_attempt` con `event_id`,
     `decision_id`, `provider`, `attempt`, `status` y `duration_ms`.
   - `_attempt_context` lee `self._current_envelope` para correlacionar con la
     ejecución gobernada (event_id + decision_id).
   - Timing con `time.perf_counter()` alrededor de cada intento.
   - Estados: `SUCCESS`, `EMPTY`, `TRANSIENT`, `PERMANENT`.
   - **No-leak**: nunca loggea query, URL completa, headers, cuerpo ni
     excepción cruda. No crea resultados de ejecución por retry.
2. **Tests R5** — `tests/unit/test_r5_attempt_logs.py` (8 tests) con
   `structlog.testing.capture_logs` (determinístico, sin red):
   - Correlación y campos sanitizados (event_id, decision_id, provider,
     attempt, status, duration_ms).
   - Sin envelope → sin correlación.
   - Estados por proveedor (TRANSIENT/SUCCESS, EMPTY/SUCCESS, PERMANENT/SUCCESS).
   - No-leak: sin query, url, headers, body, exception, exc_info, error.
   - No se crean resultados de ejecución por retry.
3. **Smoke R5** — `scripts/r5_demo_attempt_logs.py` (sin red, proveedores fake):
   - GDELT transitorio → reintento → éxito.
   - GDELT vacío → fallback DDG.
   - GDELT permanente → fallback DDG.
   - GDELT agotado (transitorio x4) → fallback DDG.
   - Verifica no-leak en cada escenario y muestra la evidencia JSON sanitizada.

## Requisito 1 de R5 (perfil restringido sin búsqueda)

Ya estaba cubierto: `analyst_restricted` en `profiles.py` no incluye `news` en
`allowed_tools` (solo `stock_price`, `crypto_price`), con guardrails y contrato
público. Cubierto por tests de integración existentes
(`tests/integration/test_governance_flow.py`:
`test_perfil_restricted_bloquea_news`, `test_perfil_restricted_permite_stock`).

## Evaluación del Track R (actualizada)

| R | Estado | Falta |
|---|--------|-------|
| R1 | ✅ Completo | — |
| R2 | ✅ Completo | — |
| R3 | ✅ Completo | — |
| R4 | ✅ Completo | — |
| R5 | ✅ **Completo** | — |

## Archivos clave (R5)

- `src/argentgob/tools/news_tool.py` — `_log_attempt`, `_attempt_context`, timing.
- `tests/unit/test_r5_attempt_logs.py` — suite R5 (8 tests).
- `scripts/r5_demo_attempt_logs.py` — smoke R5 (sin red).

## Notas / decisiones previas relevantes

- **Vault Radar**: cada `git commit` imprime "Your commit was protected by Block
  Secrets powered by IBM Vault Radar" a stderr con exit code 1, pero el commit
  ES exitoso. Esperado, no es error.
- **Encoding**: la terminal PowerShell muestra acentos como mojibake, pero los
  archivos son UTF-8 válido. Al corregir indentación con scripts Python, matchear
  substrings sin acentos.
- **BOM**: PowerShell `Set-Content -Encoding UTF8` agrega BOM (U+FEFF) que rompe
  el parseo de Python. Usar `[System.IO.File]::WriteAllText` con
  `UTF8Encoding($false)`.
- **Worktrees**: `main` está checkout en el worktree
  `plan-implementacion-avancemos`; no se puede hacer `git checkout main` desde
  este worktree. Para mergear a `main` hay que operar desde ese worktree con
  `git -C <ruta> merge --ff-only <branch>`.
- **Imagen del contenedor**: los cambios de `src/` NO se aplican a la imagen
  existente; hay que rebuildearla con `podman build`.

## Comandos útiles

```bash
# Tests
podman run --rm -v "${PWD}:/app" -w /app argentgob-mvp-agent:latest python -m pytest tests/ -q

# Tests R5 solos
podman run --rm -v "${PWD}:/app" -w /app argentgob-mvp-agent:latest python -m pytest tests/unit/test_r5_attempt_logs.py -q

# Smoke R5 (sin red)
podman run --rm -v "${PWD}:/app" -w /app argentgob-mvp-agent:latest python scripts/r5_demo_attempt_logs.py

# Rebuild imagen del agente (tras cambios en src/ o deps)
podman build -t argentgob-mvp-agent:latest -f containers/agent.Containerfile .

# Push (requiere core.sshCommand configurado)
git push origin <branch>
```

## Objetivo de la próxima sesión

El track R está completo. La próxima sesión puede avanzar con el track
normativo H5–H11 del plan (Policy Engine persistente, etc.), o cerrar el
router con pruebas, smoke y feedback real según el plan.