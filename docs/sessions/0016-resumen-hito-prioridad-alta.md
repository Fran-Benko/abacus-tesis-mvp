# Sesión 0016 — Hito de prioridad alta: criterios, HITL e2e, PG real, concurrencia y sprints

> **Estado:** ✅ completado y liberado.
> **Rama:** `agents/hito7-prioridad-alta`.
> **Base:** `main` @ `ad05c86` (Fase 6).

---

## 1. Objetivo

Completar la **prioridad alta pendiente** del `docs/plan-continuacion.md`:

1. Criterios de aceptación de H1–H4 y H8–H11.
2. Verificar HITL end-to-end con el modelo activo.
3. Detallar relaciones y cardinalidades.
4. Crear Sprint Backlogs con tareas, esfuerzo y dependencias.

Además, atacar los sesgos persistentes documentados en
`docs/analytics/0004-analisis-tests-integracion.md` (3.5-HITL, PostgreSQL real,
concurrencia).

---

## 2. Qué se implementó

### 2.1 Conexión en memoria reutilizable (`tests/integration/_mem_db.py`)

Mini-PostgreSQL que implementa el subconjunto del contrato psycopg usado por
los servicios del flujo: `governance_decisions`, `governance_events`,
`risk_predictions`, `approval_requests`, `approval_resume_tokens`,
`execution_results`, `policy_decisions`, `audit_chain` y las vistas
`v_governance_console`, `v_approval_console`, `v_risk_evidence`. Cada conexión
comparte el mismo `store` (como una DB real) y es thread-safe (RLock).

### 2.2 HITL end-to-end con el modelo activo (`test_hitl_end_to_end.py`)

Cierra el sesgo 3.5. Reproduce con componentes reales (solo la conexión
sustituida):

```
predicción MEDIUM (RiskPredictor real, forzada a MEDIUM)
  → approval PENDING + token de resume (ApprovalService real)
  → hold de argumentos (InMemoryArgumentHold real)
  → HookAborted HUMAN_APPROVAL_REQUIRED (la tool NO ejecuta)
  → resolución APPROVED por auditor (ResolutionService + evidencia H8)
  → GovernedResumeService.resume(token)  (capability de un solo uso)
  → ExecutionOrchestrator.execute_governed() (ejecuta la tool 1 vez, SUCCESS)
  → reflejo en dashboard: HITL/PREDICTED_MEDIUM_RISK en v_governance_console
    y predicción MEDIUM en v_risk_evidence (divergence CONSISTENTE)
  → INV-05: sin payload crudo persistido.
```

### 2.3 PostgreSQL real (`test_postgres_real.py`, 6 tests, skip sin TEST_DATABASE_URL)

Valida contra una DB real (`argentgob_test`, migrada con Alembic a head 006):
migraciones en head, tablas clave, constraints de unicidad/CHECK (incluye
rechazo funcional de `action` inválido → `CheckViolation`), grants mínimos de
roles de consola (sin SELECT sobre `policy_decisions`), flujo real LOW→PASS
con persistencia y lectura vía vista sanitizada, y `v_risk_evidence` sin
columnas de payload.

### 2.4 Concurrencia (`test_concurrency.py`, 3 tests)

- **Aislamiento**: dos agentes en paralelo (threads con barrera) → 4
  decisiones independientes, sin colisión de `event_id`, dashboard refleja
  ambos.
- **Idempotencia H6**: misma `idempotency_key` → misma approval (sin dup).
- **Digests**: cada decisión conserva su `payload_digest` sin contaminación.

### 2.5 Documentación

- **Criterios de aceptación H1–H4 y H9–H11** en `modulo2-entrega.md` §3.2
  (formato 3 Cs, Confirmación medible): envelope, sanitizador, ABAC,
  guardrails, CrewAI, dashboard y suite.
- **Relaciones y cardinalidades** en `modulo2-entrega.md` §3.5 (tabla completa
  con invariantes) — complementa `docs/analytics/0003` §2.2.
- **Sprint Backlogs descompuestos** en `modulo2-entrega.md` §3.3: 4 sprints
  (94 PH) con tareas, esfuerzo y dependencias + cierre del hito (9 PH).
- **`docs/plan-continuacion.md` §7**: prioridad alta marcada completada.

---

## 3. Verificación

- Suite completa (sin `TEST_DATABASE_URL`): **263 passed + 6 skipped**.
- Con `TEST_DATABASE_URL` (argentgob_test): **269 passed**.
- Tests de integración: **26 passed + 6 skipped** (incluye HITL e2e, PG real y
  concurrencia).
- Baseline previo: 259 passed → +10 tests nuevos (4 no requieren PG + 6 PG).

---

## 4. Cómo reproducir

```bash
# Suite completa (PG real se salta sin TEST_DATABASE_URL)
docker run --rm -v "${PWD}:/app" -w /app \
  -e DATABASE_URL="postgresql+psycopg://argentgob_app:dev-password-local@<ip>:5432/argentgob" \
  -e ENVIRONMENT=TEST -e LOG_LEVEL=WARNING \
  argentgob-mvp-agent:latest python -m pytest tests/ -q

# Con PostgreSQL real (req.: crear argentgob_test y migrar a head)
docker run --rm -v "${PWD}:/app" -w /app \
  -e TEST_DATABASE_URL="postgresql+psycopg://argentgob_app:dev-password-local@<ip>:5432/argentgob_test" \
  -e ENVIRONMENT=TEST -e LOG_LEVEL=WARNING \
  argentgob-mvp-agent:latest python -m pytest tests/integration/test_postgres_real.py -v
```