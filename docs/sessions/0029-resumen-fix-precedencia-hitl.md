# Sesión 0029 — Corrección de precedencia HITL y defectos del dashboard

**Fecha:** 2026-11-11
**Rama:** `agents/av-04-fix-hitl-sensitivity`
**Alcance:** corregir el cierre `PARTIAL` del caso financiero ejecutado desde el
dashboard y los defectos de las vistas del dashboard. No cierra AV-04: las
fronteras PII/IPI siguen pendientes.

## 1. Síntoma reportado

El caso financiero lanzado desde la vista **Finanzas en vivo** cerraba en
`PARTIAL` con el informe incompleto (`[Error al obtener noticias]` en las tres
noticias) y, a continuación, las demás secciones del dashboard dejaban de
funcionar.

## 2. Diagnóstico

### 2.1 El dashboard no estaba roto

Las seis vistas (`/overview`, `/audit`, `/predictive`, `/model-health`,
`/operations`, `/live-finance`) respondían HTTP 200 y renderizaban sin
excepciones (Streamlit `AppTest` por página y recorrido de navegador). El fallo
aparente de las otras secciones era de **resolución de DSN**: sin
`ARGENTGOB_DB_ROLE=dashboard`, `get_db_url()` intentaba el secreto de runtime
(no montado en el contenedor del dashboard) y caía al `DATABASE_URL` de `.env`
apuntando a `localhost`, con conexión rechazada.

### 2.2 Causa real del `PARTIAL`

El run real (`cf6a4814…`) produjo 65 eventos: 20 `tool.requested`, 20
`governance.decided` (18 `HITL` con `PREDICTED_MEDIUM_RISK`, 2 `PASS`), 18
`approval.pending` y solo 2 ejecuciones (`stock_price`, `crypto_price`).

El predictor clasificaba sistemáticamente `news` como `MEDIUM` (124 `MEDIUM`,
10 `HIGH`, 7 `LOW` en la muestra consultada). El motor determinista **ya**
devolvía `PASS` porque `POL-analyst-001` declara `sensitivity_limit = MEDIUM`
para `analyst`/`news`/`EXTERNAL_SEND` y `MEDIUM ≤ MEDIUM`. Pero
`GovernanceMiddleware.pre_hook` elevaba toda predicción `MEDIUM` a `HITL` sin
consultar ese límite. Cada elevación lanzaba `HookAborted`, CrewAI lo trataba
como error de tool y reintentaba: de ahí las 18 approvals por run y el cierre
`PARTIAL` por la regla de agregación §4 del contrato.

No había approvals duplicadas por `event_id` (269 `event_id` distintos en 269
filas): cada reintento generaba un evento nuevo. El defecto era la elevación
incondicional, no la deduplicación.

### 2.3 Defectos secundarios encontrados

- **Sesgo train/serve** en `_json_depth`: devolvía `0` para escalares mientras
  el generador del corpus devolvía `1`.
- **Extrapolación del modelo**: consultas reales de 4 caracteres frente a un
  rango entrenado de 13–66; el modelo extrapolaba sin aviso.
- **`ArrowTypeError`** en el detalle de auditoría: `pd.DataFrame([det]).T`
  mezclaba `int` y `str` en la columna «Valor».
- **`UPDATE` faltante** en `risk_predictions`: el upsert idempotente de
  `PredictionStore` usa `ON CONFLICT … DO UPDATE`, que PostgreSQL rechaza sin
  privilegio `UPDATE`; la migración 008 solo otorgó `SELECT, INSERT`.

## 3. Cambios implementados

| Archivo | Cambio |
|---|---|
| `src/argentgob/core/decision.py` | `PolicyDecision` expone `sensitivity_limit` (constructor, `to_dict`, `from_dict`). |
| `src/argentgob/module_c/deterministic_evaluator.py` | Propaga `best.policy.sensitivity_limit` en la decisión. |
| `src/argentgob/module_a/governance.py` | `_risk_rank()` local; la precedencia solo eleva `HIGH`/`MEDIUM` cuando la predicción **supera** el límite declarado. |
| `src/argentgob/analytics/features.py` | `_json_depth()` devuelve `1` para escalares (paridad con el corpus). |
| `src/argentgob/analytics/predictor.py` | Guarda de extrapolación: rangos por tool desde el corpus; fuera de `[lo − margen, hi + margen]` con `margen = max(25 % del ancho, 2.0)` la predicción se degrada al motor determinista. |
| `src/argentgob/dashboard/pages/audit_hitl.py` | Detalle construido con valores stringificados; sin `ArrowTypeError`. |
| `src/argentgob/db/connection.py` | `_db_role()` lee `ARGENTGOB_DB_ROLE` (default `runtime`). |
| `compose.yaml` | `ARGENTGOB_DB_ROLE: dashboard` en el servicio dashboard. |
| `migrations/versions/011_av04_risk_predictions_update.py` | `GRANT UPDATE ON risk_predictions TO argentgob_runtime`. |
| `src/argentgob/db/init.py`, `src/argentgob/live/readiness.py`, `scripts/verify_stack_av02.py`, `tests/unit/test_av02_stack.py`, `tests/integration/test_postgres_real.py` | Revisión esperada `011`. |
| `scripts/e2e_fase4_predictive.py` | Registro de versión del modelo con `role="admin"` (escritura administrativa). |
| `scripts/e2e_h8_console.py` | Limpieza con `role="admin"` (runtime no tiene `DELETE`). |
| `tests/unit/test_agent_server.py` | Fixture neutraliza `AGENT_API_SERVICE_TOKEN` **y** `AGENT_API_SERVICE_TOKEN_FILE`. |
| `tests/unit/test_analytics_integration.py` | Consulta de `test_precedencia_medium_hitl` dentro del rango entrenado; nuevo `test_guarda_extrapolacion_degrada_a_fallback`. |

## 4. Evidencia

### 4.1 Gates

| Gate | Resultado |
|---|---|
| `scripts/verify_agent_live.py --milestone AV-01 --with-llm` | **PASS** — 86 tests + 1 smoke LLM real |
| `scripts/api_check_agent_live.py --base http://agent:8090 --service-token-file …` | **16/16** |
| `scripts/verify_live_ui_av03.py` | **G-AV-03: 18 passed, 0 failed** |
| `scripts/verify_stack_av02.py` | **G-AV-02: 24 passed, 0 failed** |
| `scripts/e2e_h8_console.py` (DB aislada `argentgob_av04_h8`) | **23 passed, 0 failed** |
| `scripts/e2e_fase4_predictive.py` | **6 passed, 0 failed** |
| `scripts/e2e_fase5_evidence.py` | **19 passed, 0 failed** |
| `python -m argentgob.audit.cli` | `OK: la cadena de auditoría es íntegra.` |
| `scripts/demo_end_to_end.py --real` | 5 escenarios con outcomes esperados |

### 4.2 Suite

- `pytest tests/ -q -p no:cacheprovider`: **474 passed, 10 skipped**.
- `pytest tests/integration -q -p no:cacheprovider`: **52 passed, 8 skipped**.

Los 10 skips son los mismos de AV-03: 7 de `test_postgres_real.py` (sin
`TEST_DATABASE_URL`), 2 de `test_dashboard_apptest.py` (sin `DATABASE_URL`) y 1
de `test_agent_live_llm.py` (sin `DATABASE_URL`).

### 4.3 Verificación del síntoma original

- Run real vía API interna: `RUNNING → SUCCEEDED`, 2 tool calls, **0 approvals**.
- `policy_decisions` recientes: `news` bajo `POL-analyst-001` → `PASS`
  (`ALLOWED`); `HITL`/`HIGH` solo bajo la política más estricta
  `POL-analyst-news`.
- Approvals creadas en la ventana de 25 minutos: **1** (antes ~18 por run).
- Dashboard: seis rutas HTTP 200, sin `ArrowTypeError` ni `Traceback` en logs.

## 5. Limitaciones registradas

- La guarda de extrapolación **no** corrige el sesgo de etiquetado del corpus:
  `news` sigue teniendo 540/1486 filas `MEDIUM`. Solo evita extrapolar fuera del
  rango entrenado y degrada al motor determinista (falla cerrada).
- El sesgo train/serve de `_json_depth` se corrigió en runtime; el corpus
  congelado no se regeneró. Regenerarlo exige versionar el cambio y reevaluar
  el modelo.
- `POST /runs/{id}/resume` sigue siendo AV-05: la UI muestra approvals
  pendientes pero no las resuelve.
- El estado de runs vive en memoria del proceso padre; un reinicio pierde runs.
- AV-04 (fronteras PII/IPI) **no** queda cerrado por esta sesión. La migración
  011 solo resuelve el privilegio de `risk_predictions`.
- El E2E H8 sobre la DB compartida sigue arrojando 22/23 por acumulación de
  approvals vencidas; la evidencia se tomó sobre `argentgob_av04_h8`, creada,
  migrada, sembrada y eliminada en esta sesión.
