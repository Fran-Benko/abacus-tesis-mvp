# Sesión 0026 — AV-01: servicio HTTP, supervisor y eventos

**Fecha:** 2026-11-10. **Hito:** AV-01. **Gate:** G-AV-01 → **PASS**.
**Rama:** `agents/av-01-servicio-supervisor-eventos`. **Base:** `fca7663` (AV-00).

## 1. Alcance implementado

| Unidad del hito | Implementación |
|---|---|
| API interna tipada, validación de payload/perfil/actor y reserva/idempotencia atómicas | `src/argentgob/live/schemas.py` (pydantic estricto, `extra="forbid"`), `src/argentgob/live/api.py` (FastAPI, un worker), `RunSupervisor.submit` con reserva bajo lock |
| Supervisor de un proceso por run; estado aislado, timeouts y terminación confirmada | `src/argentgob/live/supervisor.py` (`RunSupervisor`, `SocketIpc`, `spawn_subprocess`), `src/argentgob/live/child.py` |
| Callback explícito, canal IPC cerrado y eventos por tentativa; buffer/TTL/cursor acotados | `src/argentgob/core/observer.py` (`GovernanceObserver`), `src/argentgob/live/events.py` (`EventBuffer`, `QueueObserver`, allowlist) |
| Proyección de informe/errores y estados parciales; sin verbose/stdout | `src/argentgob/live/runner.py` (`project_result`, `_DecisionTally`), `build_analysis_crew(verbose=False)` |
| Gate agregado ligero reutilizando pytest | `scripts/verify_agent_live.py --milestone AV-01` |

Además: `ToolCallEnvelope.build(..., run_id=...)` correlaciona `session_id` con el run (brecha 4 de AV-00), y el PEP emite `tool.requested`, `governance.decided`, `tool.started`, `tool.completed`/`tool.failed` y `approval.pending` por un observador inyectado.

## 2. Decisiones de contrato

1. **Canal IPC explícito, no `multiprocessing.Queue`.** El hijo se lanza como `python -m argentgob.live.child` con `stdin/stdout/stderr` en `DEVNULL` y se conecta a un socket loopback efímero con handshake de token de un solo uso. Un cliente sin token no entrega mensajes (probado). Esto satisface «protocolo IPC de esquema cerrado» y evita que stdout se convierta en eventos.
2. **Cupo liberado después de confirmar la muerte del hijo.** `_finish` termina el proceso (`join` → `terminate` → `kill`) y recién entonces publica el estado terminal y libera `active_run_id`. Un observador que ve el run terminado ve el cupo libre.
3. **Agregación de estado según §4.** `SUCCEEDED` solo si todas las tentativas fueron PASS y hay resultado; con acciones retenidas/bloqueadas y resultado → `PARTIAL`; sin ninguna ejecución autorizada → `BLOCKED`. El smoke real terminó `PARTIAL` porque el predictor elevó tentativas a HITL.
4. **`admin` fuera de la API de runs** (AV-I05): el catálogo de perfiles por escenario es cerrado y `admin` no está incluido.
5. **Actor por cabecera autenticada**, nunca por el cuerpo. Con `AGENT_API_SERVICE_TOKEN` configurado, la ausencia o el valor incorrecto produce 401; fuera de LOCAL/TEST sin token, 503 (fail-closed).
6. **`/ready` separa configuración de capacidades.** `ready` refleja configuración mínima; `capabilities` informa LLM y cupo, para no confundir «proceso roto» con «dependencia ausente».

## 3. Evidencia ejecutada

Árbol verificado: rama `agents/av-01-servicio-supervisor-eventos` sobre `fca7663`, con cambios sin commitear al momento de la medición. Imagen `localhost/argentgob-mvp-agent:latest` reconstruida (`1032921271dd`).

| Verificación | Comando | Resultado |
|---|---|---|
| Gate AV-01 (sin LLM) | `python scripts/verify_agent_live.py --milestone AV-01` | `collected: 73`, `73 passed`, `result: PASS` |
| Gate AV-01 (con LLM real) | `python scripts/verify_agent_live.py --milestone AV-01 --with-llm` | `collected: 84`, `84 passed` + `1 passed` (LLM), `result: PASS` |
| Smoke LLM real (pytest) | `pytest tests/integration/test_agent_live_llm.py -m llm_local` | `1 passed in 87.98s` |
| Smoke LLM real (evidencia) | `python scripts/e2e_agent_live_smoke.py --asset MSFT` | `state: SUCCEEDED`, 65 eventos, 20 decisiones, `===== E2E AV-01: OK =====` |
| Contrato HTTP desplegado | `python scripts/api_check_agent_live.py` | `16 passed, 0 failed` |
| Suite completa | `pytest tests/ -q -p no:cacheprovider` | **396 passed, 10 skipped** |
| PG real | `pytest tests/integration/test_postgres_real.py` con `TEST_DATABASE_URL` | `7 passed` |
| E2E H8 (DB aislada) | `python scripts/e2e_h8_console.py` | `23 passed, 0 failed` |
| E2E Fase 4 | `python scripts/e2e_fase4_predictive.py` | `6 passed, 0 failed` |
| E2E Fase 5 | `python scripts/e2e_fase5_evidence.py` | `19 passed, 0 failed` |
| Corpus | `python scripts/validate_corpus.py` | `RESULTADO: OK` |
| Demo determinista | `python scripts/demo_end_to_end.py` | 5 escenarios OK/BLOCK/HITL |
| CLI financiera con LLM real | `python -m argentgob.agent.main AAPL --profile analyst` | `agent_run_complete status=SUCCESS` |

Los 10 skips son los 7 de `test_postgres_real.py` sin `TEST_DATABASE_URL`, los 2 de `test_dashboard_apptest.py` sin `DATABASE_URL` y el nuevo `test_agent_live_llm.py` sin `DATABASE_URL` en la corrida sin red.

### Evidencia del smoke real (sanitizada)

```
run_id: f20be408-0a21-4e22-b830-7e968b59e416
state: SUCCEEDED
duration_ms: 92094.3
event_count: 65
event_kinds: run.accepted, run.started, tool.requested, governance.decided,
             approval.pending, tool.started, tool.completed, run.finished
result_keys: domain_status, limitations, summary
predictor_status: NOT_VALIDATED_FOR_DOMAIN
```

Correlación verificada en PostgreSQL: `policy_decisions.session_id` coincide con el `run_id` del run (`f20be408-…`, 20 filas), lo que cierra la brecha 4 de AV-00.

### Contrato HTTP verificado contra el servicio desplegado

`health 200`, `ready 200`, `perfil admin rechazado (422)`, `campo extra rechazado (422)`, `actor ausente rechazado (401)`, `admisión 202`, `idempotencia devuelve el mismo run`, `clave conflictiva 409`, `run de otro actor 404`, `estado terminal alcanzado (RUNNING → PARTIAL)`, `run.accepted`/`run.finished`/`governance.decided` presentes, `sin argumentos operativos en eventos`, `resultado proyectado presente`, `página acotada por limit`.

## 4. Limitaciones registradas

1. **`PARTIAL` es el resultado esperado del escenario financiero actual.** El predictor eleva varias tentativas a HITL; esas acciones quedan retenidas y no se ejecutan. No es un fallo: es la regla de agregación del contrato §4 aplicada correctamente.
2. **Sin resume interactivo.** `POST /runs/{id}/resume` no existe todavía; las aprobaciones quedan PENDING y el run cierra `PARTIAL`. Es AV-05.
3. **Estado en memoria del proceso padre.** Un reinicio del servicio pierde runs, eventos e idempotencia; el contrato lo declara (404 tras reinicio, sin simular recovery).
4. **Un run activo global.** El cupo es único por diseño de este hito; la concurrencia se prueba como rechazo (409), no como paralelismo.
5. **El E2E H8 sobre la DB compartida sigue dando 22/23** por acumulación de approvals PENDING (hallazgo de AV-00). La evidencia de gate se tomó sobre `argentgob_av01_test`, creada, migrada y sembrada para esta sesión y eliminada al finalizar.
6. **`fastapi`/`uvicorn` se declaran como dependencias directas** en `pyproject.toml` al implementar la API, según lo previsto por el hito. La imagen se reconstruyó antes de validar.

## 5. Archivos

**Nuevos**

- `src/argentgob/core/observer.py`
- `src/argentgob/live/__init__.py`, `events.py`, `schemas.py`, `supervisor.py`, `runner.py`, `child.py`, `api.py`, `server.py`
- `tests/unit/test_run_events.py`, `test_run_supervisor.py`, `test_agent_server.py`, `test_pep_observer.py`, `test_run_aggregation.py`
- `tests/integration/test_agent_live_flow.py`, `test_agent_live_llm.py`
- `scripts/verify_agent_live.py`, `scripts/e2e_agent_live_smoke.py`, `scripts/api_check_agent_live.py`

**Modificados**

- `src/argentgob/core/envelope.py` (`run_id` → `session_id`)
- `src/argentgob/module_a/governance.py` (observador, `run_id`, eventos de frontera)
- `src/argentgob/module_a/governed_tool.py` (`tool.started`)
- `src/argentgob/agent/crew.py` (`run_id`, `observer`, `verbose`)
- `pyproject.toml` (`fastapi`, `uvicorn`, marker `llm_local`)

## 6. Próximo hito

**AV-02 — Compose e inicialización reproducibles.** Depende de G-AV-01 (PASS). Debe cerrar las brechas 1 (seed en el inicializador) y 6 (identidad HITL configurada), convertir `agent` en API persistente sin `stdin/tty`, declarar healthchecks/readiness coherentes y probar arranque desde DB vacía, upgrade y segundo arranque. Las brechas 2, 3 y 5 siguen abiertas para AV-05/AV-04.
