# Resumen de sesión 0020 — Demo end-to-end del producto

**Hito:** Demo completa de principio a fin (gobernanza de agentes financieros).
**Rama:** `agents/demo-end-to-end`.
**Fecha:** 2026-11-08

---

## Problema resuelto

El producto tenía funcionalidad completa (PEP, policy engine, guardrails, HITL,
auditoría, orquestación gobernada) pero **no existía una forma de mostrar el
flujo completo de principio a fin**: cómo el agente llama a una tool, cómo el
middleware la gobierna y clasifica (BLOCK/HITL/PASS), cómo se resuelve el HITL
con un auditor y cómo la ejecución reanudada sigue gobernada.

Este hito entrega `scripts/demo_end_to_end.py` + `scripts/demo_lib.py`: una demo
narrativa con logs estructurados que ejecuta **5 escenarios reales** sobre los
componentes reales del middleware, en memoria (reproducible, sin red ni LLM) o
persistiendo en PostgreSQL con `--real`.

---

## Diseño

### `scripts/demo_lib.py` (nuevo)
- `DemoStore`: conexión inyectable (memoria vía `_mem_db.py`, o PostgreSQL real
  via `get_connection`) con `restore()` para no contaminar otras pruebas.
- `_wiring()`: arma el middleware real (`GovernanceMiddleware` + `ABACEvaluator`
  + `GuardrailEngine` + `AuditWriter` + `InMemoryArgumentHold` +
  `ApprovalService` + `ApprovalNotifier` + `FeatureBuilder` + `RiskPredictor` +
  `PredictionStore`).
- `_con_prediccion(mw, clase)`: context manager que fuerza la predicción del
  predictor real y **restaura** `predict` al salir — cada escenario es una
  ejecución independiente del PEP (corrige la contaminación entre escenarios).
- 5 `EscenarioDemo`:
  1. `EscenarioBenigna` — consulta AAPL, predicción LOW → PASS, la tool ejecuta,
     todo auditado.
  2. `EscenarioBloqueoPrediccion` — predicción HIGH forzada → BLOCK
     (`PREDICTED_HIGH_RISK`), la tool NO ejecuta (INV-02).
  3. `EscenarioGuardrail` — query con inyección SQL (`DROP TABLE`) → BLOCK
     con `INJECTION_PATTERN_DETECTED` (razón de guardrail, no de predicción).
  4. `EscenarioSensibilidad` — policy engine declarativo real
     (`DeterministicEvaluator` + `policies/analyst.yaml`): la política
     `POL-analyst-001` (news) declara `sensitivity_limit: MEDIUM`; predicción
     HIGH → BLOCK `SENSITIVITY_EXCEEDED`.
  5. `EscenarioHitl` — flujo HITL completo: predicción MEDIUM → approval
     PENDING + token de resume 1-uso → HookAborted (tool no ejecuta) →
     resolución APPROVED por auditor → resume → ejecución gobernada 1 vez.

### `scripts/demo_end_to_end.py` (nuevo)
- `main()`: imprime narrativa + resumen por escenario; `--real` persiste en PG.
- Añade el repo raíz a `sys.path` para correr como `python scripts/demo_end_to_end.py`.

---

## Correcciones de fidelidad durante el desarrollo

- **Contaminación del predictor**: un escenario con predicción forzada HIGH
  quedaba montado en el predictor compartido y los escenarios siguientes lo
  heredaban. Solución: `_con_prediccion` (context manager que restaura).
- **Guardrail tapado por la predicción**: la precedencia del PEP
  (BLOCK determinista > HIGH > MEDIUM > LOW) hace que una predicción HIGH
  bloquee antes de llegar a los guardrails. El escenario de guardrail fuerza
  predicción LOW para alcanzar la evaluación del guardrail (que es la razón
  del bloqueo).
- **Sensibilidad no ejercida por el middleware**: el wiring oficial usa
  `ABACEvaluator` (allow-list), que no evalúa `sensitivity_limit`. El escenario
  4 demuestra el `DeterministicEvaluator` (policy engine declarativo real de la
  Unidad 4) directamente, con las políticas YAML del perfil como fuente.

---

## Bug real de producto expuesto por la demo `--real` (FK violations)

La demo en modo memoria no valida FKs; al correr contra PostgreSQL real (**`--real`**),
el flujo HITL falló con `ForeignKeyViolation`. La causa es un bug latente del
producto:

- `approval_requests`, `execution_results` y `audit_chain` tienen FKs a
  `policy_decisions(event_id)` / `policy_decisions(decision_id)` (migración 002),
  pero **ningún componente de producción insertaba en `policy_decisions`**:
  `AuditWriter.record_decision` solo escribía en `governance_decisions`, y
  `scripts/e2e_h8_console.py` era el único que poblaba `policy_decisions`
  (fuera del runtime).

Fixes aplicados:

1. **`AuditWriter.record_decision` persiste en `policy_decisions`** (round-trip
   H5 completo: action, risk_level, reason_codes, matched_policy_ids,
   obligations, transform_spec, etc.) además de `governance_decisions` — en la
   misma conexión, `ON CONFLICT (decision_id) DO NOTHING`.
2. **`_elevate_hitl` persiste la decisión ANTES de crear la approval** (la FK
   `fk_approval_event_id` exige que `policy_decisions.event_id` exista).
3. **`ConsoleQueryService.list_decisions` parsea las columnas JSON** de la vista
   (`reason_codes`, `matched_policy_ids`, `conflicts`, `obligations`) para
   cumplir el contrato de la API (listas, no strings JSON).
4. **El demo reutiliza el `event_id` de la approval** en el envelope de la
   ejecución gobernada, para que `execution_results.event_id` cumpla la FK
   `fk_execution_event_id` en PG real (la ejecución es el mismo evento aprobado).

Prueba de regresión: `test_record_decision_persiste_policy_decisions`
(`tests/unit/test_demo_end_to_end.py`).

---

## Verificación

- `tests/unit/test_demo_end_to_end.py` (nuevo): 7 tests — uno por escenario +
  smoke test del flujo completo + regresión de `policy_decisions`.
- Suite completa en contenedor: **312 passed + 7 skipped** (7 nuevos del demo).
- PG real (con `TEST_DATABASE_URL`): **7 passed**.
- Demo completa en memoria **y** con `--real` contra PostgreSQL: 5/5 escenarios
  con los outcomes esperados.
- `ruff check` limpio sobre los archivos nuevos (`scripts/demo_*.py`,
  `tests/unit/test_demo_end_to_end.py`, `src/argentgob/db/audit.py`,
  `src/argentgob/console/service.py`).

### Salida de la demo (modo memoria)

```
ESCENARIO: Consulta benigna (PASS)
outcome: OK: spy_executed:1

ESCENARIO: Bloqueo por predicción HIGH (BLOCK)
outcome: BLOQUEADO: blocked:PREDICTED_HIGH_RISK

ESCENARIO: Bloqueo por guardrail (inyección SQL)
outcome: BLOQUEADO: blocked:INJECTION_PATTERN_DETECTED

ESCENARIO: Bloqueo por sensibilidad (SENSITIVITY_EXCEEDED)
outcome: BLOQUEADO: SENSITIVITY_EXCEEDED (sensitivity_limit MEDIUM superado)

ESCENARIO: Flujo HITL completo (aprobación humana)
outcome: HITL COMPLETO: blocked:HUMAN_APPROVAL_REQUIRED → approved por
         auditor-1 → resume (token 1-uso) → ejecución gobernada:SUCCESS
```

---

## Cómo correrla

```bash
# En memoria (reproducible, sin red ni LLM)
docker run --rm -v "${PWD}:/app" -w /app -e DATABASE_URL=... \
  -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO \
  --network bridge --entrypoint python argentgob-mvp-agent:latest \
  scripts/demo_end_to_end.py

# Persistiendo en PostgreSQL (stack real)
docker run --rm -v "${PWD}:/app" -w /app -e DATABASE_URL=... \
  -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO \
  --network bridge --entrypoint python argentgob-mvp-agent:latest \
  scripts/demo_end_to_end.py --real
```
