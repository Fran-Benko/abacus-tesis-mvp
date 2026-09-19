# Resumen de sesión — H7 Auditoría detectable ante manipulación

Fecha: 2026-09-19
Rama: `agents/feature-h7-auditoria-detectable`
Plan: `mvp-plan-argentgob-v0.3-ampliado.md` (fuente normativa, NO está en el repo; está en `~/Downloads/`)

> Consideraciones generales del proyecto (comandos de verificación, topología
> Podman, trampas recurrentes y estado): **[PROJECT_CONSIDERATIONS.md](../PROJECT_CONSIDERATIONS.md)**.

## Objetivo cumplido

Implementar **H7 — Auditoría detectable ante manipulación** del plan. H7 quedó
**completo**: append transaccional con payload canónico y encadenamiento SHA-256;
protección de la tabla `audit_chain` (trigger append-only + grants + app NO
owner); y verificador + CLI que recorren la cadena desde el génesis. La cadena es
**tamper-evident**, no inmutable (límite declarado del MVP: sin checkpoint
externo no se garantiza la detección de una reescritura completa o truncado final
coherente por owner/superusuario; no hay KMS/firma/checkpoint externo). Código +
tests + migración aplicada + verificación E2E real contra PostgreSQL.

## Estado del repositorio

- Rama de trabajo: `agents/feature-h7-auditoria-detectable`.
- Working tree: paquete `src/argentgob/audit/` nuevo + migración 003 + 3 tests
  unit + 1 test integración. Merge a `main` pendiente (fast-forward).

### Configuración SSH (sigue vigente)

```bash
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe'
```

**Usar forward slashes.** Sin esto, `git push`/`git fetch` fallan.

## Baseline de tests

- **161 passed** (150 unit + 11 integración) vía:
  `podman run --rm -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/ -q -p no:cacheprovider`
- Unit: baseline 126 → +24 H7 = **150 passed**.
- Integración: baseline 6 → +5 H7 = **11 passed**.
- Ruff NO está instalado en la imagen del agente.

## Qué se implementó (H7)

1. **Paquete `src/argentgob/audit/`** — auditoría detectable ante manipulación:
   - `canonical.py` — payload canónico (solo campos normativos H7: event_type,
     entity, digest, decision/approval, outcome, actor permitido, timestamp;
     nunca raw de argumentos/comentarios/errores, INV-05). Serialización
     canónica reutilizando la estrategia del envelope
     (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).
     Encadenamiento `chain_hash = sha256(prev_hash + "\n" + payload)` sobre
     UTF-8; representación `sha256:` + 64 hex (71 chars, cabe en `String(80)`);
     `GENESIS_HASH = "sha256:" + "0"*64`. `recompute_chain_hash` normaliza el
     payload para el verificador.
   - `append.py` — `AuditChainAppender`: serializa lectura de cabeza + append en
     la misma transacción (`autocommit=False`), resuelve la secuencia
     transaccionalmente (`head.sequence + 1` o `1`), calcula el hash y hace el
     INSERT. Append + cambio durable atómicos cuando comparten PostgreSQL. No
     mantiene lock durante aprobación humana ni llamada externa (el `FOR UPDATE`
     se libera en el commit). Si la evidencia previa no puede persistirse, no se
     ejecuta (`AuditAppendError`). Timestamp calculado en Python y persistido en
     `recorded_at` para que el verificador pueda recalcular el hash.
   - `verify.py` — `AuditChainVerifier`: recorre la cadena desde el génesis y
     comprueba secuencia sin saltos, enlace prev-hash, hash recalculado desde el
     payload persistido, hashes únicos y referencias de entidad. NO repara ni
     recalcula silenciosamente la historia alterada: reporta la primera
     ubicación (sequence) y un motivo sanitizado.
   - `cli.py` — `python -m argentgob.audit.cli`: exit 0 solo si la cadena es
     íntegra; exit 1 con `sequence` + motivo sanitizado si se detecta
     manipulación; exit 2 ante error de infraestructura.
2. **Migración `003_h7_audit_chain.py`** — protección de `audit_chain`:
   - Columna `payload` (Text) para persistir el payload canónico y permitir el
     recálculo del hash.
   - Rol dedicado `argentgob_audit_owner` (NOLOGIN) que pasa a ser owner de la
     tabla (el app NO es owner).
   - Trigger append-only (`audit_chain_append_only`) que impide UPDATE/DELETE/
     TRUNCATE (complementa los grants; la protección real, ya que el app se
     conecta como superusuario del esquema).
   - Grants mínimos: app solo INSERT+SELECT, auditor solo SELECT; se revoca
     UPDATE/DELETE/TRUNCATE a los roles de runtime.
3. **Tests** — `test_h7_canonical.py` (8), `test_h7_append.py` (6),
   `test_h7_verify.py` (10) unitarios con `FakeConn`/`FakeCursor`; y
   `tests/integration/test_h7_audit_chain.py` (5) con una conexión falsa en
   memoria que persiste filas entre llamadas (round-trip append→verify, detección
   de manipulación de payload/chain_hash, CLI exit 0/1).

## Verificación E2E realizada

Contra el stack Podman (red `argentgob-mvp_default`, `DATABASE_URL` a
`postgres:5432`):

- Migración 003 aplicada (`alembic upgrade head` → `003 (head)`).
- `audit_chain` con columna `payload`, owner `argentgob_audit_owner`, triggers
  `trg_audit_chain_append_only` (UPDATE/DELETE) y `trg_audit_chain_no_truncate`
  (TRUNCATE). Grants verificados: app INSERT+SELECT, auditor SELECT.
- Trigger probado de verdad: UPDATE y DELETE sobre `audit_chain` lanzan
  `audit_chain is append-only: UPDATE/DELETE/TRUNCATE forbidden`.
- Round-trip real: appender escribió 3 entradas encadenadas y el verificador
  retornó `ok=True`.
- Manipulación real: se alteró el payload de la sequence 2 (desactivando el
  trigger como owner) y el CLI reportó
  `INVALID: cadena manipulada en sequence=2 motivo=hash recalculado no coincide (manipulación)`
  con exit 1.
- Cadena vacía: CLI retorna `OK: la cadena de auditoría es íntegra.` exit 0.
- Se limpió la data de prueba; `audit_chain` quedó vacía.

## Próximo hito

**H8 — Consola sanitizada y resolución autorizada** según el plan. H7 deja la
infraestructura de auditoría lista; H8 conecta la UI/consola (vista sanitizada,
filtros parametrizados, detalle HITL) y la resolución autorizada.

## Archivo de consideraciones

- Se actualizó `docs/PROJECT_CONSIDERATIONS.md`: baseline de tests actualizado a
  161 passed, estado alcanzado (H7 completo), migración 003 (head) y sección
  3.3.1 con el comando del CLI de verificación de la cadena.