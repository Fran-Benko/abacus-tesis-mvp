# Resumen de sesión — H5 Policy Engine persistente implementado y validado E2E

Fecha: 2026-09-18
Rama: `agents/h5-policy-engine-persistente-implementacion` (mergeada a `main` en `d74376f`)
Plan: `mvp-plan-argentgob-v0.3-ampliado.md` (fuente normativa, NO está en el repo; está en `~/Downloads/`)

> Consideraciones generales del proyecto (comandos de verificación, topología
> Podman, trampas recurrentes y estado): **[PROJECT_CONSIDERATIONS.md](../PROJECT_CONSIDERATIONS.md)**.

## Objetivo cumplido

Implementar **H5 — Policy Engine persistente** del plan. H5 quedó **completo**:
carga de políticas desde PostgreSQL con caché limitada, evaluación determinista
(BLOCK > HITL > PASS, `POLICY_CONFLICT`), round-trip de decisión completo entre
modelo, JSON Schema, JSON, DB y vista, y la persistencia/migraciones (tablas y
roles separados, clave compuesta `(policy_id, policy_version)`, vista
`v_governance_console`). Código + migración aplicada + tests; validado E2E con
análisis real de una empresa con 0 errores de auditoría.

## Estado del repositorio

- Rama de trabajo: `agents/h5-policy-engine-persistente-implementacion`.
- `main` → `d74376f` — **sincronizada con `origin/main`** (H5 mergeado y pusheado).
- `origin/main` y branch, ambos en `d74376f67c91cbf4cfdce9554546e295b0f5a9f4`.
- Working tree: limpio (ambos worktrees).

### Configuración SSH (sigue vigente)

```bash
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe'
```

**Usar forward slashes.** Sin esto, `git push`/`git fetch` fallan.

## Baseline de tests

- **102 tests pasan** (baseline 87 → +15 H5) vía:
  `podman run --rm -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/ -q -p no:cacheprovider`
- Tests de integración: **6 passed**.
- Ruff NO está instalado en la imagen del agente.

## Commits de H5 (branch `agents/h5-policy-engine-persistente-implementacion`)

| Commit | Mensaje |
|--------|---------|
| `5eb8dc5` | migración 002 — tablas (governance_events/policies separadas, policy_decisions, execution_results, approval_requests, approval_resume_tokens, audit_chain), clave compuesta `(policy_id,policy_version)`, vista `v_governance_console`, roles |
| `d74376f` | H5: policy engine — carga persistente, decisión determinista y round-trip |

Merge a `main`: **fast-forward** `0351b0d..d74376f`.

## Qué se implementó (H5)

1. **Migración 002** — `migrations/versions/002_h5_policy_engine.py` (490 líneas):
   amplifica `governance_events`/`governance_policies`, clave compuesta
   `(policy_id, policy_version)`, crea `policy_decisions`, `execution_results`,
   `approval_requests`, `approval_resume_tokens`, `audit_chain`, vista
   `v_governance_console` (sanitizada) y roles. Aplicada en `002 (head)`; todas
   las tablas/vista/roles verificados en el Postgres del stack. Deja preparado el
   almacenamiento de H6/H7 (approvals, tokens, audit_chain) sin implementar sus
   servicios.
2. **Caché limitada** — `src/argentgob/module_c/policy_store.py`: `PolicyStore`
   con TTL (30 s) y `valid_until`; ante caída de DB solo READ explícita con caché
   vigente; WRITE/DELETE/DDL/EXTERNAL_SEND/CODE_EXEC bloquean.
3. **Decisión determinista** — `deterministic_evaluator.py`: BLOCK > HITL > PASS,
   `POLICY_CONFLICT` para obligaciones incompatibles.
4. **Round-trip de decisión** — `core/decision.py` + `policy.py`:
   `decision_id`, `action`, `risk_level`, `reason_codes`, `policy_id`,
   `policy_version`, `policy_digest`, `matched_policy_ids`,
   `effective_priority`, `conflicts`, `obligations`, `transform_spec`,
   `decided_at`, `expires_at` entre modelo/JSON/DB/vista.
5. **Errores** — `core/errors.py`: `RiskLevel` + ReasonCodes H5
   (`TOOL_NOT_REGISTERED`, `NO_POLICY_MATCHED`, `POLICY_EXPIRED`,
   `SENSITIVITY_EXCEEDED`, `INSUFFICIENT_AUTHORIZATION`,
   `INCOMPATIBLE_OBLIGATIONS`, `DB_UNAVAILABLE`).
6. **Fix en el camino** — `policy_store.py` con helpers `_parse_json_list`/
   `_parse_json_object` mal indentados (añadidos fuera de clase). Re-indentados
   a nivel de clase; ahora compila y funciona.

## Verificación E2E realizada

Analisis real de empresa (AAPL) + seguimiento de logs.

- El stack había sido levantado desde el worktree `h5-...` (con `models/` vacío)
  → `llama-server` en crash-loop ("failed to open GGUF file"). Se recreó
  `llama-server` desde el worktree correcto (`plan-implementacion-avancemos`,
  dueño de `models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`). El Postgres usa
  named volume, por lo que la DB quedó intacta.
- Ajuste de red: con `--network host` el flujo corre completo (noticias reales de
  Zacks/Yahoo) pero los writes a DB fallan (conexión rechazada), porque postgres
  no publica `:5432` al host. Con `--network argentgob-mvp_default` y
  `DATABASE_URL` a `postgres:5432`, la corrida terminó con:
  - **0 audit errors**, `agent_run_complete STATUS=SUCCESS`, exit 0.
  - `governance_events`: news `d739987c`, stock_price `c1c8c3e0`.
  - `governance_decisions`: correlacionadas (PASS/ALLOWED; `POL-analyst-news`,
    `POL-analyst-stock_price`).
  - Logs `news_provider_attempt` sanitizados: GDELT TRANSIENT → fallback DDG,
    correlacionando `event_id`; sin raw/bearer/headers.
- Tablas H5 (`policy_decisions`, `execution_results`, `audit_chain`) existen pero
  vacías (0 filas) — al integrarse `DeterministicEvaluator` en el flujo de
  ejecución (H6/H7) se poblarán. `v_governance_console` une `policy_decisions` +
  `execution_results` (hoy 0 filas).

## Próximo hito a implementar

**H6 — Aprobación humana durable y ejecución gobernada** (aprobación HITL en el
loop). Es lo que sigue en el plan y encaja naturalmente: la migración 002 ya dejó
preparadas `approval_requests` y `approval_resume_tokens`.

### Qué implica (según el plan, sección "6. H6")

1. **Hold, creación e idempotencia** — `InMemoryArgumentHold` (en proceso, nunca
   disco/DB/telemetría), `MAX_HELD_ARGUMENT_BYTES` acotado y ≤
   `MAX_PAYLOAD_BYTES`; crear/recuperar PENDING con idempotencia y transacción;
   token opaco de un solo uso, almacenando solo SHA-256.
2. **Hook y respuesta original** — HITL persiste PENDING antes de notificar y
   aborta la tentativa: al agente `status=blocked`,
   `execution_status=NOT_EXECUTED`, `error_code=HUMAN_APPROVAL_REQUIRED`,
   `resume_mode=NEW_GOVERNED_EXECUTION`, `tool_called=false`, `approval_ref` no
   sensible; sin bearer ni éxito falso.
3. **Notifier y resolución** — notifier recibe solo approval/evento y
   `sanitized_preview`; resolver exige identidad autorizada de auditor y evidencia
   vigente; update transaccional condicionado a PENDING, digest y `expires_at`.
4. **Resume y capability** — revalidar approval/digest/policy, consumir token con
   operación SQL condicional única, crear `ExecutionCapability` interna de un solo
   uso; executor rechaza capability ajena/expirada/reutilizada.
5. **Ejecución, UNKNOWN y recuperación** — guard/claim previo al executor;
   resultados SUCCESS/FAILED/UNKNOWN; ante pérdida de confirmación tras efecto:
   UNKNOWN + reconciliación, nunca retry automático; reemisión gobernada solo si
   no hay resultado del evento lógico y no hubo ejecución indeterminada.

### Dependencias clave

- Requiere H5 válido (lo está). **Reutilizar una única interfaz pública:
  `GovernedResumeService.resume(token_id, presented_token)`**.
- Requiere integrar el **`DeterministicEvaluator`/`PolicyStore`** en el flujo de
  ejecución vivo para poblar las tablas H5 (hoy vacías).
- H8 conecta la UI/consola; H6 usa un proveedor de notificación fixture de
  desarrollo.
- Garantía local: como máximo una solicitud por evento lógico en el protocolo
  probado, **no** exactly-once global.

### Recomendación de arranque

Empezar por la Unidad "hold, creación e idempotencia" (InMemoryArgumentHold +
creación idempotente de approvals sobre `approval_requests` ya migradas),
manteniendo el flujo HITL integrado con el `DeterministicEvaluator` para que las
tablas H5 se pueblen. Crear branch `agents/h6-aprobacion-durable-hitl` y seguir
la regla: push/merge solo tras pasar la suite unitaria y el test integral.

## Referencias de sesiones anteriores

- [0004-resumen-r5-integracion.md](0004-resumen-r5-integracion.md) — patrón de
  sesión y comandos E2E corrrectos.