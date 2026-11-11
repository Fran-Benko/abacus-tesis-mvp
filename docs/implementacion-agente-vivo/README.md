# Plan v3 — Agente en vivo, despliegue y escenarios públicos

**Fecha:** 2026-11-10. **Estado:** documentación rectora preparada; implementación pendiente.

Esta versión convierte la revisión v2 en un paquete implementable con contratos, unidades, pruebas y gates por hito. Reemplaza v1/v2 como hoja de ruta de estos tres frentes; no reemplaza los hitos históricos H1–H16 ni certifica sus cierres. Los IDs **AV-00…AV-06** pertenecen exclusivamente a esta ampliación.

## 1. Lectura y autoridad

| Documento | Qué gobierna | Cuándo leer |
|---|---|---|
| [Hitos y entregables](hitos.md) | DAG, unidades, entrada, tests, gate y entregables por AV | Antes de iniciar o cerrar cualquier hito |
| [Contratos y flujos](contratos.md) | API, identidad, eventos, estados, argumentos/resultados, HITL e invariantes | Antes de modificar ejecución, observación o UI |
| [Catálogo de escenarios](escenarios.md) | Inputs/outputs, perfiles, tools, políticas, guardrails y demos LLM | Antes de implementar un dominio o una demo |
| [Configuración y despliegue](despliegue.md) | Topología, configuración por servicio, inicialización y fallos | Antes de tocar compose, imágenes o Settings |
| [Pruebas y cierre](verificacion.md) | Casos identificados, runners, evidencia y aceptación | Al diseñar tests y en todo gate |

Orden obligatorio: [consideraciones del proyecto](../PROJECT_CONSIDERATIONS.md) → este índice → hito activo → contratos y documentos aplicables → [sesiones recientes](../sessions/). [La hoja de ruta predictiva](../plan-continuacion.md) y [Módulo 2](../modulo2-entrega.md) son contexto histórico/académico, no el backlog de esta ampliación.

**Reglas de autoridad:** las invariantes y el plan normativo externo siguen vigentes. Un conflicto con ellos bloquea la unidad afectada hasta documentar una decisión. Dentro de este paquete, contratos gobierna interfaces; escenarios gobierna permisos/casos; despliegue gobierna configuración; verificación gobierna el oráculo; hitos gobierna dependencias/entregables. No cambiar un oráculo después de ver un fallo para obtener PASS. Una modificación semántica exige actualizar todos los documentos afectados y repetir las pruebas dependientes.

## 2. Baseline: qué existe y qué no

Referencia histórica más reciente: [sesión 0022](../sessions/0022-resumen-rediseno-v4.md), **334 passed + 9 skipped**, y [sesión 0023](../sessions/0023-resumen-llama-server-gpu-e2e-agente-real.md), GPU/CDI y E2E financiero con LLM local del 2026-11-09. Migraciones presentes hasta **007**; la revisión aplicada en DB debe comprobarse en el entorno de implementación.

Existe: Crew financiero, tools de noticias/precios, sanitización determinista de telemetría, políticas YAML, predictor financiero, H6/H8, auditoría encadenada, dashboard multipágina y demo de cinco escenarios sin LLM. `test_crewai_real.py` prueba tools CrewAI con proveedor fake; su nombre no acredita inferencia contra llama.cpp.

No existe todavía lo que aquí se especifica: catálogo público de cinco dominios, control IPI de documentos y enlace de resume al proceso propietario del hold. El servidor HTTP de runs existe desde AV-01, el agente de compose es una API persistente desde AV-02 y el timeline incremental con UI financiera en vivo existe desde AV-03. No presentar los objetivos restantes como baseline implementado.

La transformación operativa actual hace normalización de strings, no masking PII. El runner de la demo determinista copia logs al estado al finalizar: no reutilizar ese mecanismo como prueba de tiempo real; la vista Finanzas en vivo (AV-03) usa eventos de la API, no logs. La imagen del agente ya copia `data/` (AV-02), por lo que el predictor carga sin bind mount. Corregir las brechas restantes solo en su hito correspondiente.

## 3. Arquitectura y alcance mínimo

- PostgreSQL 16, llama.cpp local, agente HTTP de una instancia y Streamlit. Inicializador run-once para Alembic + políticas.
- Un proceso hijo por run y un run activo global; polling, callback explícito y buffer acotado. Sin brokers, Redis, WebSockets, plugins ni workflow distribuido.
- Mantener CLI y demo financiera actuales. Agregar sección de agente real, no reemplazarlas.
- Cinco contextos públicos con LLM real y **proveedores sintéticos**. Sin conexiones institucionales reales, envíos reales, adjudicación de beneficios ni datos personales reales.
- El predictor financiero conserva su comportamiento; para nuevos dominios, control determinista y rótulo «predictor no validado». Sin reentrenamiento obligatorio.
- HITL: aprobación humana y ejecución única de la acción retenida. No checkpoint/replay automático del razonamiento del Crew tras resume o reinicio.
- No exponer cadena de pensamiento, prompts internos ni stdout de CrewAI. La timeline muestra operaciones verificables.

## 4. Trazabilidad de requisitos

| Requisito | Hitos | Contrato/catálogo | Pruebas/gate |
|---|---|---|---|
| R-A: stack completo y reproducible | AV-02, AV-06 | Despliegue | T-A01…T-A08; G-AV-02 |
| R-B: agente local en vivo | AV-01, AV-03 | API/eventos/UI | T-B01…T-B09; G-AV-01/03 |
| R-C1: trámites y PII | AV-04, AV-05 | `tramites` y frontera PII | T-P01…T-P06; D-01/02 |
| R-C2: beneficios y supervisión | AV-05 | `beneficios`, HITL | T-H01…T-H07; D-03/04 |
| R-C3: comunicaciones e IPI | AV-04, AV-05 | `comunicaciones`, liberación | T-I01…T-I05; D-05/06 |
| R-C4: auditoría trazable | AV-05 | `auditoria` | T-D01…T-D03; D-07/08 |
| R-C5: consultas normativas | AV-05 | `normativa` | T-J01…T-J04; D-09/10 |
| R-V: validación transversal | AV-00, AV-06 | Invariantes y evidencia | T-R01…T-R06; G-AV-06 |

Demos **D-01…D-12** son nuevas ejecuciones LLM reales propuestas; no se suman artificialmente a los cinco escenarios stub existentes ni prueban eficacia predictiva pública.

## 5. Seguimiento inicial

| Hito | Estado | Resultado de gate |
|---|---|---|
| AV-00 — Baseline operativo y contratos | **Completo** | **PASS** — [sesión 0025](../sessions/0025-resumen-av-00-baseline-contratos.md) |
| AV-01 — Servicio, supervisor y eventos | **Completo** | **PASS** — [sesión 0026](../sessions/0026-resumen-av-01-servicio-supervisor-eventos.md) |
| AV-02 — Stack reproducible | **Completo** | **PASS** — [sesión 0027](../sessions/0027-resumen-av-02-compose-inicializacion.md) |
| AV-03 — UI financiera en vivo | **Completo** | **PASS** — [sesión 0028](../sessions/0028-resumen-av-03-ui-financiera-en-vivo.md); corrección de precedencia HITL en [sesión 0029](../sessions/0029-resumen-fix-precedencia-hitl.md) |
| AV-04 — Fronteras PII/IPI | Pendiente | No ejecutado |
| AV-05 — Dominios, demos reales y HITL | Pendiente | No ejecutado |
| AV-06 — Regresión y entrega | Pendiente | No ejecutado |

Actualizar esta tabla solo con referencia a evidencia de [cierre](verificacion.md); documentación redactada no equivale a un hito cerrado. AV-00, AV-01, AV-02 y AV-03 se cerraron con pruebas de aplicación ejecutadas (ver [sesión 0025](../sessions/0025-resumen-av-00-baseline-contratos.md), [sesión 0026](../sessions/0026-resumen-av-01-servicio-supervisor-eventos.md), [sesión 0027](../sessions/0027-resumen-av-02-compose-inicializacion.md) y [sesión 0028](../sessions/0028-resumen-av-03-ui-financiera-en-vivo.md)); los hitos restantes siguen sin evidencia.

## 6. Hallazgos de AV-00 que condicionan los hitos siguientes

El cierre de AV-00 verificó el baseline y detectó seis brechas que **no** se corrigieron en ese hito. Son prerequisito explícito de los hitos indicados; no deben presentarse como resueltas.

| Brecha verificada | Evidencia | Hito que la cierra |
|---|---|---|
| El inicializador solo corre `alembic upgrade head`; no ejecuta `seed_policies`. Una DB nueva queda con 0 políticas y toda tool bloquea con `NO_POLICY_MATCHED`. | DB aislada recién migrada: 0 filas en `governance_policies`; smoke LLM real → 30 `BLOCK`/`NO_POLICY_MATCHED`. Tras seed: `PASS`/`HITL` reales. | **Cerrada en AV-02**: `python -m argentgob.db.init` ejecuta upgrade + seed + verificación; gate 24/24 (T-A01, T-A04) |
| `GovernedResumeService._revalidate` valida solo estado y expiraciones; **no** revalida política vigente, versión/digest ni guardrails, pese a lo que afirma su docstring. | Introspección del código: `policy_version`, `policy_digest`, `guardrail`, `argument_digest` ausentes en `_revalidate`. | AV-05 (T-H06) |
| `AuditChainAppender` no está cableado al runtime: solo lo instancian tests. | DB del stack: 74 `policy_decisions` vs 1 fila en `audit_chain` (de un E2E H7 manual). | AV-05 (T-D01) |
| No existe correlación de run: `session_id` es un `uuid4()` por envelope y `run_id` solo vive en logs. | `core/envelope.py::build`; `agent/main.py`. | **Cerrada en AV-01**: `ToolCallEnvelope.build(..., run_id=...)` usa el `run_id` como `session_id`; verificado en DB (`policy_decisions.session_id` = `run_id` del smoke). |
| No hay persistencia de liberación de contenido (`content_status`/`control_action`). | `execution_results` solo tiene `status`; ninguna tabla tiene columnas de contenido. | AV-04 (migración `008`) |
| La identidad HITL no está configurada en el despliegue: `identity_fixture_roles={}` y `authorized_auditor_roles=[]`. | Sonda en el contenedor del dashboard: `resolve(...)` → `blocked/unauthorized`. | **Cerrada en AV-02**: `compose.yaml` declara `IDENTITY_FIXTURE_ROLES` y `AUTHORIZED_AUDITOR_ROLES`; sonda en el contenedor devuelve `has_auditor=True` (T-B03, T-H02) |

Además: `max_json_depth`, `max_json_nodes`, `privacy_scan_timeout_ms` y `agent_profile` existen en `Settings` pero no se aplican; `argentgob_app` es superusuario; y `containers/agent.Containerfile` no copia `data/`, por lo que el predictor reporta `available=False` dentro de la imagen. Detalle y decisiones en [sesión 0025](../sessions/0025-resumen-av-00-baseline-contratos.md).
