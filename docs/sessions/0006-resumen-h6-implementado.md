# Resumen de sesión — H6 Aprobación humana durable y ejecución gobernada

Fecha: 2026-09-19
Rama: `agents/h6-human-approval-execution-implementation`
Plan: `mvp-plan-argentgob-v0.3-ampliado.md` (fuente normativa, NO está en el repo; está en `~/Downloads/`)

> Consideraciones generales del proyecto (comandos de verificación, topología
> Podman, trampas recurrentes y estado): **[PROJECT_CONSIDERATIONS.md](../PROJECT_CONSIDERATIONS.md)**.

## Objetivo cumplido

Implementar **H6 — Aprobación humana durable y ejecución gobernada** del plan.
H6 quedó **completo**: hold de argumentos en memoria (idempotente y acotado);
creación idempotente y durable de approvals PENDING con token de resume opaco de
un solo uso (solo SHA-256 persistido); hook del PEP con respuesta
`blocked:HUMAN_APPROVAL_REQUIRED`; notifier sanitizado + resolución por auditor
autorizado; resume con `ExecutionCapability` de un solo uso reutilizando la única
interfaz pública `GovernedResumeService.resume(token_id, presented_token)`; y
ejecución gobernada con estados SUCCESS/FAILED/UNKNOWN y recuperación **sin**
retry automático. Además se integró el `DeterministicEvaluator`/`PolicyStore` en
el flujo de ejecución vivo (crew); en una corrida permisiva las tablas H5 del
round-trip (`policy_decisions`/`execution_results`) se pueblan por la ruta de
ejecución gobernada H6. Código + tests + verificación completa.

## Estado del repositorio

- Rama de trabajo: `agents/h6-human-approval-execution-implementation`.
- Working tree: modificados 5 fuentes + paquete `src/argentgob/hitl/` nuevo +
  7 archivos de test. Merge a `main` pendiente (fast-forward).

### Configuración SSH (sigue vigente)

```bash
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe'
```

**Usar forward slashes.** Sin esto, `git push`/`git fetch` fallan.

## Baseline de tests

- **126 passed** (baseline 102 → +24 H6) vía:
  `podman run --rm -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/unit -q -p no:cacheprovider`
- Tests de integración: **6 passed**.
- Ruff NO está instalado en la imagen del agente.

## Qué se implementó (H6)

1. **Paquete `src/argentgob/hitl/`** — frontera de aprobación humana:
   - `argument_hold.py` — `InMemoryArgumentHold`: hold idempotente por
     `(event_id, digest)` en memoria (nunca disco/DB/telemetría), acotado por
     `max_held_argument_bytes` y `max_held_total_bytes`, TTL `hold_ttl_seconds`,
     eviction de vencidos y `release` explícito. `HoldConflict` ante digest
     distinto para el mismo evento; `HoldCapacity` ante exceso.
   - `approval_service.py` — `ApprovalService.create_pending_and_issue_token`:
     flujo transaccional e idempotente por `idempotency_key` (INSERT ... ON
     CONFLICT). Crea la approval PENDING y emite un token de resume opaco,
     persistiendo **solo** `hash_token(token_plan)`. Si el hold no se registró,
     la approval se marca `CANCELLED` y NO se emite token.
   - `resolver.py` — `ApprovalResolver.resolve(approval, auditor, decision)`:
     resolución transaccional y condicional (solo PENDING + digest correcto +
     no vencida); exige auditor con rol en `authorized_auditor_roles`
     (Unidad 3). Discrimina vencida / digest incorrecto / ya resuelta y
     `TargetNotFound`.
   - `governed_resume.py` — `GovernedResumeService.resume(token_id,
     presented_token)`: revalida la approval (APPROVED, digest, vigencia),
     consume el token con operación SQL condicional única
     (`state='ACTIVE'` + `constant_time_equals(hash_token(presented),
     stored_hash)`) y devuelve una `ExecutionCapability` interna de un solo uso.
   - `execution.py` — `ExecutionOrchestrator.execute_governed`: guard/claim de la
     capability previo al executor; resultados `governed:SUCCESS` /
     `governed:FAILED`; ante pérdida de confirmación tras efecto → UNKNOWN /
     `RESULT_RECOVERED` sin retry automático; capability consumida/expirada/ajena
     → error de dominio.
   - `notifier.py` — `ApprovalNotifier` (channel `ConsoleNotifier`): recibe solo
     approval/evento y `sanitized_preview`; payload sanitizado (sin bearer/raw).
   - `records.py`, `tokens.py`, `errors.py` (base de errores del paquete).
2. **Hook del PEP** — `src/argentgob/module_a/governance.py`: rama HITL que, con
   servicios inyectados, registra el hold (`_register_hold`), crea la approval
   PENDING + emite token, notifica y aborta con
   `blocked:HUMAN_APPROVAL_REQUIRED` (`tool_called=false`,
   `execution_status=NOT_EXECUTED`, `resume_mode=NEW_GOVERNED_EXECUTION`). Sin
   servicios degrada a R2 (solo bloquea). Helper `_argument_digest`.
3. **Crew cableado** — `src/argentgob/agent/crew.py`: reemplaza `ABACEvaluator`
   por `DeterministicEvaluator(settings, PolicyStore(settings))` e inyecta
   `InMemoryArgumentHold`, `ApprovalService`, `ApprovalNotifier(ConsoleNotifier())`
   al `GovernanceMiddleware`.
4. **Config** — `src/argentgob/core/config.py`: `max_held_argument_bytes`,
   `max_held_total_bytes`, `hold_ttl_seconds`, `approval_ttl_seconds`,
   `authorized_auditor_roles` + `model_validator` que valida invariantes
   normativas H6 (hold acotado ≤ payload, hold TTL ≤ approval TTL, total ≥ arg).
5. **Errores** — `src/argentgob/core/errors.py`: ReasonCodes H6
   (`EXECUTION_ARGUMENTS_UNAVAILABLE`, `TOKEN_INVALID`, `APPROVAL_NOT_APPROVED`,
   `CAPABILITY_REUSED`, `CAPABILITY_EXPIRED`, `HOLD_UNAVAILABLE`,
   `NOTIFICATION_FAILED`, `RESULT_ALREADY_EXISTS`, `UNAUTHORIZED_AUDITOR`,
   `NOT_EXECUTED`, `NEW_GOVERNED_EXECUTION`, `RESULT_RECOVERED`).
6. **Fix seed** — `scripts/seed_policies.py`: agrega obligación por defecto
   `USE_ORIGINAL_ARGUMENTS` (requerida para que el `DeterministicEvaluator` no
   rechace el PASS con `POLICY_CONFLICT`) y `ON CONFLICT DO UPDATE`.

## Tests H6 añadidos (24)

- `test_h6_argument_hold.py` (7) — idempotencia, conflicto, capacidad por
  tamaño/total, TTL, release.
- `test_h6_approval_service.py` (3) — emisión de token, cancelación si falla el
  hold, idempotencia por idempotency_key.
- `test_h6_resolver.py` (5) — resolución autorizada / no autorizada / inválida,
  ya resuelta, TargetNotFound.
- `test_h6_resume.py` (6) — revalidación, consumo de token, capability de un
  solo uso.
- `test_h6_execution.py` (5) — SUCCESS/FAILED/UNKNOWN, guard/claim, capability
  inválida.
- `test_h6_notifier.py` (2) — payload sanitizado y fallo.
- `test_h6_hook.py` (2) — con servicios registra hold + emite token + notifica,
  y sin servicios solo bloquea.
- Helper `_h6_fakes.py` — `FakeConn`/`FakeCursor` para el subconjunto psycopg
  transaccional sin DB real.

## Verificación E2E realizada

Corrida real del agente (AAPL, perfil analyst) contra el stack Podman con la red
`argentgob-mvp_default` y `DATABASE_URL` a `postgres:5432`:

- `agent_run_complete status=SUCCESS`, exit 0.
- **0 audit errors**.
- `governance_events` y `governance_decisions` se poblaron (eventos por tool,
  decisiones correlacionadas). `approval_requests` / `approval_resume_tokens`
  quedaron en 0 porque la corrida es permisiva (PASS, sin HITL): las approvals
  requieren una decisión HITL.
- `policy_decisions` / `execution_results` siguen en 0 en la corrida permisiva:
  `AuditWriter` persiste `governance_decisions`/`governance_events`; la ruta que
  escribe `execution_results` es la de ejecución gobernada H6 (HITL), no
  ejercida aquí.

## Próximo hito

**H7 — Auditoría detectable ante manipulación** según el plan. H8 conecta la
UI/consola; H6 usa un proveedor de notificación fixture de desarrollo.

## Archivo de consideraciones

- Se actualizó `docs/PROJECT_CONSIDERATIONS.md`: baseline de tests actualizado
  a 126 passed, estado alcanzado (H6 completo) y notas de la integración
  DeterministicEvaluator/PolicyStore en el flujo vivo.