# Sesión 0025 — AV-00: baseline operativo y contratos mapeados

**Fecha:** 2026-11-10. **Rama:** `agents/av-00-baseline-contratos`.
**Tipo:** verificación operativa + decisiones de contrato. Sin cambios en `src/`, tests, dependencias ni compose.
**Hito:** [AV-00](../implementacion-agente-vivo/hitos.md) — gate **G-AV-00: PASS** (con hallazgos que condicionan AV-02/04/05).

---

## 1. Baseline reproducible

### 1.1 Árbol, imágenes y stack

| Elemento | Valor verificado |
|---|---|
| Commit base | `f81bd7d` (`docs: definir plan v3 de agente en vivo y gates por hito`) |
| Worktree raíz (posee `main` y el GGUF) | `abacus-tesis-mvp` |
| Imagen agente | `localhost/argentgob-mvp-agent:latest` = `bddf21d79336` (18 h) |
| Imagen dashboard | `localhost/argentgob-mvp-dashboard:latest` = `56448baa56d5` (18 h) |
| Imagen migrator | `docker.io/library/argentgob-mvp-migrator:latest` = `ae82db6c9e70` (6 semanas) |
| Podman / Compose | 5.7.1 / Docker Compose v2.39.2 (proveedor externo) |
| Contenedores | `postgres-1` healthy (2 d), `llama-server-1` healthy (14 h), `dashboard-1` up (18 h) |
| GGUF | `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`, 4 912 898 304 B, `Q4_K - Medium`, `n_ctx=8192` |
| llama.cpp | `/health` → `{"status":"ok"}`; `/v1/models` responde con el GGUF montado |

### 1.2 Migraciones

- Presentes en el repo: `001` → `007`.
- Aplicadas en `argentgob` (DB del stack): **`007`** (`select version_num from alembic_version`).
- Aplicadas en `argentgob_test`: **`007`**.
- Aplicadas en DB aislada nueva (`argentgob_av00_test`, creada en esta sesión): `001 → 007` sin error.

### 1.3 Resultados de ejecución real

| Verificación | Comando (dentro de Podman) | Resultado |
|---|---|---|
| Suite completa | `pytest tests/ -q -p no:cacheprovider` | **334 passed, 9 skipped** (140.9 s) |
| Integración PG real | `pytest tests/integration/test_postgres_real.py` con `TEST_DATABASE_URL` → `argentgob_test` | **7 passed** |
| Corpus | `python scripts/validate_corpus.py` | `RESULTADO: OK` (3000 filas, freeze hash OK) |
| E2E Fase 4 | `scripts/e2e_fase4_predictive.py` | **6 passed, 0 failed** |
| E2E Fase 5 | `scripts/e2e_fase5_evidence.py` | **19 passed, 0 failed** |
| E2E H8 (DB compartida) | `scripts/e2e_h8_console.py` | **22 passed, 1 failed** |
| E2E H8 (DB limpia) | idem sobre `argentgob_av00_test` | **23 passed, 0 failed** |
| Cadena de auditoría | `python -m argentgob.audit.cli` | `OK: la cadena de auditoría es íntegra.` |
| Demo determinista | `scripts/demo_end_to_end.py` (sin `--real`) | 5 escenarios: `OK`, `BLOCK`×3, `HITL COMPLETO` |
| Smoke LLM real | `python -m argentgob.agent.main AAPL --profile analyst` contra `llama-server` | Inferencia real + tools ejecutadas |

**Los 9 skips son los mismos de la referencia histórica:** 7 de `test_postgres_real.py` (sin `TEST_DATABASE_URL` en la corrida sin red) y 2 de `test_dashboard_apptest.py` (sin `DATABASE_URL`). Ninguno oculta un fallo: al proveer las variables, los 7 tests PG pasan.

### 1.4 El único fallo de E2E H8 es ambiental, no una regresión

`pending_approvals(limit=10)` ordena `argued_at ASC` y la DB compartida acumula **25 approvals PENDING** de sesiones previas; la approval recién creada queda fuera de la ventana. Sobre una DB recién migrada el mismo script da **23/23**. Se registra como limitación de la evidencia sobre DB compartida, no como defecto del código.

---

## 2. Mapa de contratos → rutas reales

| Contrato ([contratos.md](../implementacion-agente-vivo/contratos.md)) | Ruta real hoy | Estado |
|---|---|---|
| Builder de dominio | `agent/crew.py::build_analysis_crew` (solo financiero, perfil fijo) | Existe; no hay builder público por escenario |
| PEP | `module_a/governance.py::GovernanceMiddleware.pre_hook` | Existe y es el punto único de enforcement |
| Transformador activo | `module_b/argument_transforms.py::transform_arguments` | Solo `strip()` de strings; **no** masking PII |
| Selección de argumentos | `select_effective_arguments` (exige exactamente una obligación) | Existe |
| Guardrails | `module_c/guardrails.py::GuardrailEngine` (`query_injection`, `topic_relevance`, `rate_limit`) | Existe; registro cerrado de 3 |
| Evaluador de política | `module_c/deterministic_evaluator.py::DeterministicEvaluator` + `PolicyStore` | Existe; fail-closed a `NO_POLICY_MATCHED` |
| Predicción | `analytics/predictor.py::RiskPredictor` (artefactos en `data/models/risk_model_v1/`) | Existe; **no** empaquetado en la imagen del agente |
| Callback de eventos | — | **No existe** (AV-01) |
| Identidad H8 | `identity/provider.py::AuditorIdentityProvider` (fixture) + `identity/evidence.py` | Existe; requiere configuración explícita |
| Resolución HITL | `console/resolution.py::ResolutionService` → `hitl/resolver.py::ApprovalResolver` | Existe |
| Hold de argumentos | `hitl/argument_hold.py::InMemoryArgumentHold` (in-process, TTL) | Existe; no sobrevive reinicio |
| Resume | `hitl/governed_resume.py::GovernedResumeService.resume` | Existe; **revalidación incompleta** (§3.2) |
| Ejecución gobernada | `hitl/execution.py::ExecutionOrchestrator.execute_governed` | Existe; capability de un uso |
| Persistencia de decisión | `db/audit.py::AuditWriter.record_decision` → `governance_decisions` + `policy_decisions` | Existe |
| Persistencia de resultado | `db/audit.py::AuditWriter.record_result` → `governance_events` | Existe |
| Cadena de auditoría | `audit/append.py::AuditChainAppender` | Existe pero **no está cableada** al PEP (§3.3) |
| Correlación de run | `envelope.session_id` = `uuid4()` por envelope | **No hay** correlación de run (§3.4) |
| Persistencia de liberación de contenido | — | **No existe** columna ni tabla (§3.5) |

---

## 3. Hallazgos que fijan decisiones de contrato

### 3.1 El inicializador no siembra políticas (bloquea AV-02)

`compose.yaml` ejecuta `migrator` como `python -m alembic upgrade head` **solamente**. `scripts/seed_policies.py` no forma parte del arranque.

Evidencia: en la DB aislada recién migrada, `governance_policies` tenía **0 filas** y el smoke LLM real produjo **30 decisiones `BLOCK` con `NO_POLICY_MATCHED`** para `news`, `stock_price` y `crypto_price`. Tras `python -m scripts.seed_policies` (8 políticas), el mismo smoke produjo `PASS`/`HITL` reales y ejecutó tools.

Consecuencia: el agente "funciona" en la DB compartida solo porque alguien sembró políticas manualmente en el pasado. Un arranque limpio queda fail-closed (correcto por INV-11) pero **inoperante**.

**Decisión:** AV-02 debe convertir el inicializador en `alembic upgrade head && python -m scripts.seed_policies` con fallo propagado (exit ≠ 0), y T-A01 debe verificar `governance_policies > 0` como postcondición. No se cambia compose en AV-00.

### 3.2 El resume no verifica política, digest ni guardrails (bloquea AV-05 / T-H06)

`GovernedResumeService._revalidate` valida **solo** `approval.state == APPROVED` y las expiraciones de approval y token. Su docstring afirma revalidar `argument_digest`, decisión de política vigente y versión compatible; el código **no** lo hace.

Verificación por introspección del código real: `policy_version: False`, `policy_digest: False`, `guardrail: False`, `argument_digest: False` en `_revalidate`. El `argument_digest` sí se compara, pero en `_require_hold` (hold vs approval), no contra la decisión persistida.

Consecuencia: una aprobación válida autoriza la ejecución aunque la política haya cambiado o un guardrail duro deba bloquear. Es exactamente el riesgo que [contratos §5](../implementacion-agente-vivo/contratos.md) marca como pendiente de AV-05.

**Decisión:** AV-05 debe implementar revalidación efectiva (política vigente + versión/digest + guardrails duros) **antes** de consumir el token, con test que cambie la política entre aprobación y resume. El docstring no es evidencia (T-H06).

### 3.3 La cadena de auditoría no está cableada al runtime (bloquea AV-05 / T-D01)

`AuditChainAppender` solo se instancia en tests. En la DB del stack hay **74 `policy_decisions`** y **1 fila en `audit_chain`**, y esa fila es de un E2E H7 manual (`event_id = e2e-h7-…`). El flujo del agente no encadena sus decisiones.

Consecuencia: `python -m argentgob.audit.cli` devuelve "íntegra" porque verifica una cadena de una sola fila; no acredita que las decisiones del agente estén encadenadas.

**Decisión:** AV-05 debe cablear el append en la frontera de decisión (misma transacción que el cambio durable cuando comparten PostgreSQL) y T-D01 debe exigir que un run real produzca filas encadenadas correlacionadas con `policy_decisions`.

### 3.4 No existe correlación de run

`ToolCallEnvelope.build` asigna `session_id = uuid4()` **por envelope**. No hay `run_id` persistido: en `agent/main.py` es solo un campo de log. Dos tools del mismo run no son correlacionables en DB.

**Decisión:** AV-01 introduce `run_id` en el contrato de eventos y lo propaga al envelope; AV-05 lo persiste para T-D01. No se agrega columna en AV-00.

### 3.5 No hay persistencia de liberación de contenido (bloquea AV-04)

Ninguna tabla tiene columnas `content_status`/`control_action`. `execution_results` solo tiene `status` (`SUCCESS`/`FAILED`/`UNKNOWN`). El contrato exige distinguir `execution_status=SUCCESS` de `content_status=WITHHELD` con evidencia durable.

**Decisión:** AV-04 requiere una migración mínima (`008`) que agregue el estado de contenido y su motivo, con prueba de upgrade sobre DB de prueba y rollback documentado. Un badge sin evidencia durable no satisface el gate.

### 3.6 La identidad HITL no está configurada por defecto

`Settings.identity_fixture_roles = {}` y `authorized_auditor_roles = []`. Ni `.env` ni `compose.yaml` los definen. Verificado en el contenedor del dashboard y por sonda directa: `ResolutionService.resolve(...)` devuelve `{'outcome': 'blocked', 'reason': 'unauthorized'}` para `auditor-1`.

Consecuencia: la UI de Auditoría y HITL no puede resolver approvals con la configuración desplegada; el E2E H8 pasa porque construye `Settings(...)` explícito.

**Decisión:** AV-02 debe declarar la configuración de identidad por consumidor (fixture solo en LOCAL/TEST, explícitamente no productiva) y T-B03/T-H02 deben probar el camino con la configuración real del stack, no con `Settings` inyectado.

### 3.7 Límites declarados que no se aplican

`max_json_depth`, `max_json_nodes`, `privacy_scan_timeout_ms` y `agent_profile` existen en `Settings` y **no se usan** fuera de `config.py`. Solo `max_payload_bytes`, `max_string_bytes` y `rate_limit_calls_per_run` tienen efecto.

**Decisión:** AV-01 aplica profundidad/nodos/tiempo en la validación de payload (T-B01) o retira los campos; no se documentan como límites activos hasta que lo estén.

### 3.8 El rol de aplicación es superusuario

`argentgob_app` tiene `rolsuper=t`, `rolcreatedb=t`, `rolcreaterole=t`. Los roles de consola son `NOLOGIN` con grants mínimos (correcto), pero el rol que usa el agente y el dashboard es superusuario.

**Decisión:** AV-02 debe separar el rol de runtime del rol de migración y verificar mínimo privilegio (T-A08). No se rota en AV-00 para no tocar el stack compartido.

### 3.9 El predictor no está empaquetado en la imagen del agente

`RiskPredictor()` dentro de la imagen reporta `available=False` (`model_dir=/app/data/models/risk_model_v1` inexistente): `containers/agent.Containerfile` no copia `data/`. El dashboard sí monta `./data:ro`.

Consecuencia: el agente en imagen degrada a fallback determinista; la predicción solo ocurre cuando se monta `data/` (como en los E2E).

**Decisión:** AV-02 monta los artefactos RO en el agente y verifica versión/digest visibles (T-A07). No se presenta el predictor como operativo en la imagen hasta entonces.

---

## 4. Oráculos y prerequisitos definidos para los hitos siguientes

| Hito | Prerequisito que AV-00 fija | Oráculo |
|---|---|---|
| AV-01 | `run_id` en contrato de eventos; límites de payload efectivos | T-B01, T-B05, T-B06 |
| AV-02 | Inicializador con seed + fallo propagado; identidad configurada; rol de runtime no superusuario; `data/` RO en agente | T-A01, T-A04, T-A07, T-A08 |
| AV-04 | Migración `008` de estado de contenido; detector sin valor | T-P01…T-P06, T-I01…T-I05 |
| AV-05 | Revalidación efectiva en resume; append de auditoría cableado; `run_id` persistido | T-H03, T-H06, T-D01, T-R05 |

---

## 5. Limitaciones de esta evidencia

- El E2E H8 sobre la DB compartida da 22/23 por acumulación de approvals PENDING; la evidencia válida es la corrida sobre DB limpia (23/23).
- El smoke LLM real se ejecutó sobre `argentgob_av00_test` (aislada) para no contaminar la DB del dashboard; la DB aislada se creó y migró en esta sesión.
- No se reconstruyeron imágenes: no hubo cambios de código. Las imágenes usadas son las del cierre anterior.
- No se ejecutaron las demos D-01…D-12 (no existen; son entregable de AV-05).
- Los hallazgos §3.1–§3.9 son **decisiones registradas**, no correcciones aplicadas. Ninguno se presenta como resuelto.

---

## 6. Resultado del gate

**G-AV-00: PASS.** Baseline reproducible con resultados y limitaciones registrados; mapa de contratos a rutas reales completo; decisiones de contrato fijadas sin bloqueos para AV-01; oráculos y prerequisitos de AV-02/04/05 definidos.

Los hallazgos §3.1–§3.9 **no** bloquean AV-01 (servicio HTTP, supervisor y eventos), que no depende de seed, cadena cableada ni estado de contenido. Sí condicionan AV-02, AV-04 y AV-05, y quedan registrados como prerequisito explícito de cada uno.

**Ningún otro gate AV queda aprobado.** AV-01…AV-06 siguen pendientes.
