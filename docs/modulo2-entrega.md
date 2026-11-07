# Entrega Módulo 2 — Prototipo Tecnológico ArgentGob-Mesh

> **Proyecto de tesis.** Toda la documentación está en español.
> Este documento describe el prototipo tecnológico **ArgentGob-Mesh**: un middleware de gobernanza perimetral para agentes CrewAI en el dominio financiero.

---

## 0. Estado verificado del prototipo

El contenido de este documento refleja el estado **real y verificado** del prototipo, ejecutado dentro de los contenedores Podman con las imágenes actualizadas (`localhost/argentgob-mvp-agent:latest`).

| Verificación | Resultado |
|---|---|
| Suite de pruebas completa (`pytest tests/`) | ✅ **253 passed** (232 unit + 21 integración) |
| Dashboard Streamlit (`:8501`) | ✅ HTTP 200 |
| LLM local llama-server (`:8080`) | ✅ HTTP 200 |
| PostgreSQL (`argentgob-mvp-postgres-1`) | ✅ Healthy |
| Cadena de auditoría (`audit_chain`) | ✅ **Íntegra** (verificador CLI: `OK`) |
| E2E consola H8 (`scripts/e2e_h8_console.py`) | ✅ **23/23 checks OK** |
| E2E capa predictiva (`scripts/e2e_fase4_predictive.py`) | ✅ **6/6 checks OK** |
| E2E evidencia académica (`scripts/e2e_fase5_evidence.py`) | ✅ **19/19 checks OK** |
| Migraciones aplicadas | ✅ 001 → 002 → 003 → 004 → 005 → 006 |

**Datos reales en la base (al momento de la verificación):**

- `policy_decisions`: 1 decisión `PASS`.
- `audit_chain`: 1 entrada encadenada (génesis).
- `governance_policies`: 6 políticas `PASS` (admin: 1, analyst: 3, analyst_restricted: 2).
- `approval_requests`: sin registros (flujo HITL sin activar en esta corrida).
- `risk_model_versions`: 1 versión activa (`risk_model_v1` / `v1`, logistic, isotonic).
- `risk_predictions`: predicciones con clase, probabilidad, latencia y vínculo a la decisión final.

> **Nota de trazabilidad:** el worktree de la rama `feat/r0-reconciliation-baseline` contiene solo la migración `001` y el alcance R0 (Módulos A/B/C + auditoría básica). Las funcionalidades **H5 (policy engine persistente), H6 (aprobación humana HITL), H7 (auditoría encadenada) y H8 (consola sanitizada y resolución autorizada)** están implementadas y verificadas en las imágenes de contenedor actualizadas (`argentgob-mvp-agent`), con migraciones `002`, `003` y `004` aplicadas en la base. La **capa predictiva (Fases 1–5)** agrega las migraciones `005` (predicciones y registro de modelos) y `006` (vista de evidencia). Este documento cubre el alcance completo del prototipo tal como corre en los contenedores.

---

## 1. Diagnóstico y Propuesta

### 1.1 Diagnóstico de la Situación Actual

El relevamiento previo evidencia que el problema **no es la "falta de un sistema"**, sino fallas de **proceso, calidad de información y control perimetral** en el uso de agentes de IA generativa en el dominio financiero:

- **Ausencia de control perimetral sobre las tool calls.** Un agente CrewAI con LLM local puede invocar herramientas externas (búsqueda web, precios de mercado) sin un punto único de enforcement que valide *qué* se ejecuta, *con qué argumentos* y *bajo qué perfil*. Esto expone a la organización a ejecuciones no autorizadas o fuera de política.
- **Riesgo de inyección (prompt/SQL).** El LLM puede ser manipulado para inyectar instrucciones o consultas maliciosas en los argumentos de las herramientas. Sin validación de dominio, una query como `ignore previous instructions` o `'; DROP TABLE policies;--` llegaría directamente al proveedor externo.
- **Fuga de datos sensibles (PII) en telemetría.** Los argumentos operativos (emails, DNIs, números de tarjeta, secretos) podían quedar registrados en logs y bases de auditoría en crudo, violando principios de minimización de datos.
- **Falta de trazabilidad auditable.** No existía un registro inmutable de *qué decisión se tomó, por qué y cuándo*, ni la posibilidad de correlacionar la decisión (PASS/BLOCK) con el resultado de ejecución. Tampoco había protección contra la manipulación retroactiva de los registros.
- **Ausencia de control de acceso diferenciado por rol.** Todos los usuarios/agentes tenían el mismo nivel de acceso, sin perfiles que limiten herramientas según el rol (analista, analista restringido, administrador).
- **Falta de límites operativos.** Sin control de tamaño de payload ni de tasa de llamadas, un agente podía saturar proveedores externos o procesar cargas desmedidas.
- **Políticas sin versionado ni vigencia.** Las reglas de acceso no tenían control de versiones, autoría, vigencia temporal ni digest de integridad, lo que impedía auditar *qué* política se aplicó en cada decisión.

### 1.2 Propuesta de Solución de Ciencia de Datos

**ArgentGob-Mesh**: un **middleware de gobernanza perimetral** que se interpone entre el agente CrewAI y sus herramientas, aplicando un pipeline de control en 3 módulos:

1. **Módulo A — Policy Enforcement Point (PEP).** Intercepta cada *tool call*, construye un `ToolCallEnvelope` canónico (separando argumentos de ejecución de argumentos de telemetría) y decide `PASS`/`BLOCK`/`HITL` de forma síncrona **antes** de que la herramienta produzca efecto.
2. **Módulo B — Sanitizador.** Enmascara datos sensibles (PII) y trunca payloads largos para la telemetría, **sin mutar** los argumentos operativos.
3. **Módulo C — ABAC + Guardrails + Policy Engine.** Evalúa la política de acceso por perfil (ABAC), aplica guardrails de dominio financiero (inyección, relevancia temática, rate limit) y resuelve la decisión de forma **determinista** con políticas versionadas y persistentes.

**Capacidades ampliadas (H5/H6/H7/H8):**

- **H5 — Policy Engine persistente:** políticas ABAC versionadas (clave compuesta `policy_id`/`policy_version`), matching exacto, precedencia `BLOCK > HITL > PASS`, obligaciones de selección de argumentos (`USE_ORIGINAL`/`USE_TRANSFORMED`), caché limitada con TTL y fail-closed ante DB caída.
- **H6 — Aprobación humana (HITL):** decisiones `HITL` que generan solicitudes de aprobación durables e idempotentes, con token de reanudación opaco de un solo uso (solo se persiste su hash SHA-256).
- **H7 — Auditoría encadenada:** cadena de auditoría *tamper-evident* con encadenamiento SHA-256, trigger append-only (bloquea UPDATE/DELETE/TRUNCATE), ownership dedicado y verificador CLI que detecta manipulación.
- **H8 — Consola sanitizada y resolución autorizada:** consola de monitoreo sin raw payload (vista `v_approval_console`), filtros parametrizados, detalle HITL y resolución autorizada con identidad (roles de aplicación `viewer`/`auditor`/`approver`/`admin` y evidencia de autenticación).

**Enfoque metodológico:** enforcement **fail-closed** (ante duda → `BLOCK`), auditoría persistente y encadenada en PostgreSQL, y dashboard de monitoreo en tiempo real. **Valor para la organización:** ejecución de agentes de IA financiera segura, auditable y conforme a políticas, con trazabilidad completa, protección de datos sensibles y detección de manipulación de evidencia.

---

## 2. Objetivos, Límites y Alcance del Prototipo

### 2.1 Objetivo del Prototipo

> **Implementar** un middleware de gobernanza perimetral que intercepte, evalúe y audite cada tool call de un agente financiero local, bloqueando ejecuciones no autorizadas o riesgosas antes de que produzcan efecto.

### 2.2 Límites del Prototipo

> **Desde** la interceptación de una tool call emitida por el agente CrewAI **hasta** la persistencia de la decisión (PASS/BLOCK/HITL), el resultado de ejecución y la entrada encadenada en la cadena de auditoría.

### 2.3 Alcance

El prototipo procesa efectivamente:

- **Enmascaramiento de PII** en telemetría (emails, PAN, DNI, secretos) y truncado de payloads largos.
- **Validación de políticas ABAC** por perfil (herramientas permitidas por rol), con políticas versionadas y vigentes.
- **Guardrails de dominio:** detección de inyección (SQL/prompt), relevancia temática financiera y rate limit.
- **Control de límites de payload** (tamaño máximo) antes del escaneo costoso.
- **Decisión determinista** con precedencia `BLOCK > HITL > PASS` y detección de conflictos de política (`POLICY_CONFLICT`).
- **Aprobación humana (HITL)** con solicitudes durables, idempotentes y tokens de reanudación de un solo uso.
- **Log de auditoría** de decisiones y resultados en PostgreSQL.
- **Cadena de auditoría encadenada** (SHA-256, append-only) con verificación de integridad.
- **Dashboard de monitoreo** en tiempo real (métricas PASS/BLOCK, latencia, distribución por herramienta).
- **Degradación segura:** fallback in-memory de políticas si la DB no está disponible; fail-open solo para observabilidad (no para enforcement).

---

## 3. Descripción del Proyecto (Documentación Técnica e Ingeniería)

### 3.1 Product Backlog (estimado en PH)

| # | Historia | PH | Prioridad |
|---|----------|----|-----------|
| H1 | Envelope canónico de tool call (separación ejecución/telemetría) | 3 | Alta |
| H2 | Sanitizador de PII y truncado de payloads | 5 | Alta |
| H3 | Evaluador ABAC por perfil con fallback in-memory | 5 | Alta |
| H4 | Guardrails de dominio (inyección, relevancia, rate limit) | 8 | Alta |
| H5 | Policy engine persistente (versión, matching determinista, caché, obligaciones) | 13 | Alta |
| H6 | Aprobación humana HITL (solicitudes durables, tokens de reanudación) | 13 | Alta |
| H7 | Auditoría encadenada tamper-evident + verificador | 13 | Alta |
| H8 | Consola sanitizada y resolución autorizada (roles + evidencia) | 3 | Media |
| H9 | Integración con CrewAI (GovernedTool) | 5 | Alta |
| H10 | Dashboard de monitoreo en tiempo real | 5 | Media |
| H11 | Suite de tests unitarios e integración | 8 | Media |

**Total estimado: 81 PH.** (3+5+5+8+13+13+13+3+5+5+8)

### 3.2 Historias de Usuario (técnica de las 3 Cs)

**H7 — Auditoría encadenada (Tarjeta)**
> Como **auditor**, quiero que la evidencia de gobernanza esté encadenada y protegida contra manipulación, para poder verificar la integridad de la historia de decisiones.

- **Conversación:** cada entrada de `audit_chain` encadena su hash contra la anterior (`chain_hash = sha256(prev_hash + payload)`); un trigger bloquea UPDATE/DELETE/TRUNCATE; el verificador recorre la cadena desde el génesis.
- **Confirmación (criterios de aceptación medibles):**
  - Dada una cadena íntegra, cuando se ejecuta el verificador, entonces retorna `OK` (exit 0).
  - Dada una entrada con `prev_hash` roto o `chain_hash` recalculado distinto, cuando se verifica, entonces se reporta `sequence` y motivo sanitizado (exit ≠ 0).
  - Dado un intento de UPDATE/DELETE/TRUNCATE sobre `audit_chain`, entonces el trigger lo rechaza.

**H5 — Policy engine persistente (Tarjeta)**
> Como **administrador de políticas**, quiero versionar y vigilar las políticas ABAC, para que cada decisión sea determinista y auditable.

- **Conversación:** las políticas se leen de `governance_policies` con clave compuesta `policy_id`/`policy_version`, matching exacto y precedencia `BLOCK > HITL > PASS`; la caché local respeta TTL y vigencia.
- **Confirmación:**
  - Dado un perfil sin permiso para una herramienta, cuando se evalúa, entonces se bloquea con `TOOL_NOT_ALLOWED` y la herramienta no se ejecuta.
  - Dadas dos políticas con obligaciones incompatibles para el mismo alcance, cuando se evalúa, entonces se produce `POLICY_CONFLICT` (cero ejecución).
  - Dada una DB caída, cuando se intenta una escritura, entonces falla cerrada (BLOCK); solo lecturas con caché vigente pueden continuar.

**H6 — Aprobación humana HITL (Tarjeta)**
> Como **operador de seguridad**, quiero revisar y aprobar/rechazar decisiones `HITL`, para que las acciones sensibles requieran intervención humana.

- **Conversación:** una decisión `HITL` crea una solicitud de aprobación durable e idempotente; si el hold en memoria se registra, se emite un token de reanudación opaco de un solo uso (solo se persiste su hash).
- **Confirmación:**
  - Dado un `idempotency_key` repetido, cuando se crea la solicitud, entonces se devuelve la existente (sin duplicados).
  - Dado un hold que falla tras crear la solicitud, entonces la solicitud se cancela y **no** se emite token (no-resumable).
  - Dado un token, cuando se persiste, entonces solo se almacena su hash SHA-256, nunca el valor plano.

**H8 — Consola sanitizada y resolución autorizada (Tarjeta)**
> Como **auditor/operador**, quiero inspeccionar decisiones y resolver solicitudes HITL desde una consola que nunca expone el payload crudo, para poder operar sin violar INV-05.

- **Conversación:** la consola (vista `v_approval_console`, paquete `src/argentgob/console/`) expone métricas, estado de la cadena, decisiones sanitizadas y detalle HITL sin `execution_arguments` ni raw argument; la resolución exige identidad (roles `viewer`/`auditor`/`approver`/`admin`) y evidencia de autenticación (`src/argentgob/identity/`).
- **Confirmación:**
  - Dado un rol sin permiso, cuando se intenta resolver, entonces se bloquea la resolución (no se muta estado).
  - Dado un intento de resolución sin evidencia de autenticación, entonces se bloquea (estado `blocked`).
  - Dada una consulta de la consola, entonces ninguna respuesta contiene `execution_arguments` ni raw payload (INV-05).

  **H1 — Envelope canónico de tool call (Tarjeta)**
  > Como **plataforma de gobernanza**, quiero representar cada tool call en un envelope canónico que separe ejecución de telemetría, para que ninguna herramienta corra antes de una decisión y el payload crudo no viaje por canales de observabilidad.

  - **Conversación:** `ToolCallEnvelope` (src/argentgob/core/envelope.py) separa `execution_arguments` (reales, solo memoria) de `telemetry_arguments` (sanitizados, para logs/DB); calcula `payload_digest` (SHA-256 del JSON canónico) y `payload_size_bytes`; la identidad del agente (`AgentIdentity`) exige id, rol y versión.
  - **Confirmación (criterios de aceptación medibles):**
    - Dado un tool call interceptado, cuando se construye el envelope, entonces contiene `event_id`, `session_id`, `payload_digest` y `payload_size_bytes` válidos.
    - Dado un envelope, cuando se verifica el digest contra los argumentos, entonces `verify_digest()` retorna `True` (y `False` si se alteran los argumentos).
    - Dada una identidad incompleta (sin id, rol o versión), cuando se evalúa `_identity_complete`, entonces se bloquea (INV-15 fail-closed).

  **H2 — Sanitizador de PII y truncado de payloads (Tarjeta)**
  > Como **plataforma de gobernanza**, quiero sanitizar PII y truncar payloads antes de la telemetría, para no filtrar datos personales en logs ni bases de datos (INV-05).

  - **Conversación:** `Sanitizer` (src/argentgob/module_b/sanitizer.py) aplica regex deterministas (EMAIL, SECRET, PAYMENT_NUMBER, POSSIBLE_DNI), trunca cadenas según `max_string_bytes` y limita profundidad/nodos JSON (`max_json_depth`, `max_json_nodes`).
  - **Confirmación:**
    - Dado un argumento con email/secret/número de pago/DNI, cuando se sanitiza, entonces esos valores se redactan y el resto permanece intacto.
    - Dado un payload que excede `max_payload_bytes`, cuando se valida el envelope, entonces el PEP bloquea con `PAYLOAD_LIMIT_EXCEEDED` (INV-14) antes del scanner costoso.
    - Dado un argumento anidado más profundo que `max_json_depth`, cuando se sanitiza, entonces se trunca de forma segura sin excepción.

  **H3 — Evaluador ABAC por perfil con fallback in-memory (Tarjeta)**
  > Como **administrador de seguridad**, quiero evaluar cada tool call contra el perfil ABAC del agente, para que ninguna herramienta no autorizada se ejecute.

  - **Conversación:** `ABACEvaluator` (src/argentgob/module_c/abac_evaluator.py) decide con `allowed_tools` del perfil; `DeterministicEvaluator` (module_c) aplica la precedencia `BLOCK > HITL > PASS`; `PolicyStore` carga políticas desde `governance_policies` con fallback in-memory si la DB no está disponible (fail-closed solo para escrituras).
  - **Confirmación:**
    - Dado un perfil sin permiso para una herramienta, cuando se evalúa, entonces se bloquea con `TOOL_NOT_ALLOWED` y la tool no se ejecuta (`call_count == 0`).
    - Dada una herramienta permitida, cuando se evalúa, entonces pasa y ejecuta.
    - Dada la DB caída, cuando se consultan políticas, entonces se usan las políticas en memoria del perfil (el enforcement no queda sin fuentes).

  **H4 — Guardrails de dominio (Tarjeta)**
  > Como **plataforma de gobernanza**, quiero aplicar guardrails de dominio (inyección, relevancia, rate limit), para bloquear intentos maliciosos o fuera de contexto antes de la ejecución.

  - **Conversación:** `GuardrailEngine` (src/argentgob/module_c/guardrails.py) ejecuta por tool call: `query_injection` (patrones SQL/shell/script en la telemetría), `topic_relevance` (dominio de la consulta) y `rate_limit` (máximo de llamadas por corrida).
  - **Confirmación:**
    - Dada una consulta con inyección SQL/shell, cuando se ejecuta el guardrail, entonces el PEP bloquea con `query_injection` y la tool no ejecuta.
    - Dada una consulta fuera del dominio financiero, cuando se evalúa `topic_relevance`, entonces se bloquea (o se exige revisión) sin ejecutar.
    - Dado que se supera `rate_limit_calls_per_run`, cuando llega la siguiente tool call, entonces se bloquea por `rate_limit`.

  **H9 — Integración con CrewAI (GovernedTool) (Tarjeta)**
  > Como **plataforma de gobernanza**, quiero envolver las herramientas CrewAI con `GovernedTool`, para que cada tool call del agente pase por el PEP sin que el dispatcher de CrewAI pueda evadirlo.

  - **Conversación:** `GovernedTool` (src/argentgob/module_a/governed_tool.py) es la clase base que corre `pre_hook` → `_execute` → `post_hook`; `src/argentgob/agent/crew.py` construye el Crew con herramientas gobernadas (`NewsTool`, `StockPriceTool`, `CryptoPriceTool`); una excepción interna del hook aborta fail-closed.
  - **Confirmación:**
    - Dada una tool CrewAI envuelta, cuando el agente la invoca, entonces `pre_hook` corre antes del executor y ninguna tool corre sin `decision_id` (INV-01).
    - Dado un BLOCK en el `pre_hook`, entonces `GovernedTool._run` aborta con `HookAborted` y la tool no ejecuta (INV-02).
    - Dado un error interno del hook, entonces se aborta fail-closed (no se confía en que el dispatcher propague la excepción).

  **H10 — Dashboard de monitoreo en tiempo real (Tarjeta)**
  > Como **analista/operador**, quiero un dashboard que muestre decisiones, eventos y métricas en tiempo real, para monitorear la gobernanza sin exponer payloads crudos.

  - **Conversación:** Streamlit (`src/argentgob/dashboard/app.py`) con auto-refresh (5 s); KPIs (total, PASS, BLOCK, latencia pre-hook), decisiones recientes, eventos, distribución por herramienta, sección H8 (consola sanitizada + resolución) y sección Fase 5 (evidencia predictiva) sobre las vistas `v_governance_console`, `v_approval_console` y `v_risk_evidence`.
  - **Confirmación:**
    - Dado un flujo de decisiones, cuando se consulta el dashboard, entonces muestra los KPIs y las decisiones recientes sin `execution_arguments` en ninguna respuesta (INV-05).
    - Dado un HITL pendiente, cuando se consulta la sección H8, entonces aparece en la lista de pending con su `argument_digest` (nunca argumentos en claro).
    - Dadas las predicciones de la Fase 5, entonces el dashboard muestra: distribución LOW/MEDIUM/HIGH, HITL/BLOCK originados por predicción y matriz/calibración del modelo.

  **H11 — Suite de tests unitarios e integración (Tarjeta)**
  > Como **equipo de desarrollo**, quiero una suite automática de tests unitarios e integración, para verificar los invariantes y el comportamiento completo de la gobernanza.

  - **Conversación:** pytest con `tests/unit` (componentes y contratos), `tests/integration` (flujos agénticos, HITL end-to-end, concurrencia, PostgreSQL real), tests de auditoría encadenada (H7), consola (H8) y capa predictiva (Fase 4–5); los E2E (`scripts/e2e_*.py`) validan el stack real contra PostgreSQL.
  - **Confirmación:**
    - Dado el repositorio, cuando se ejecuta `pytest tests/`, entonces la suite completa pasa (unit + integración) sin red ni LLM dependencias para los tests deterministas.
    - Dado un flujo agéntico (usuario con agente de IA), cuando se ejecuta la integración, entonces valida que se prediga/bloquee lo correcto y que el resultado se refleje en dashboard y auditoría.
    - Dado el stack levantado, cuando se ejecutan los E2E (`e2e_fase4_predictive`, `e2e_fase5_evidence`, `e2e_h8_console`), entonces todos los checks pasan contra PostgreSQL real.

### 3.3 Sprint Backlog (sprints iniciales)

**Sprint 1 (fundamentos):** H1, H2, H3, H4 — envelope, sanitizador, ABAC y guardrails (3+5+5+8 = **21 PH**).
**Sprint 2 (enforcement + persistencia):** H5, H7, H9 — policy engine persistente, auditoría encadenada e integración CrewAI (13+13+5 = **31 PH**).
**Sprint 3 (HITL + consola):** H6 — aprobación humana (13 PH) + H8 consola sanitizada (3 PH) + H10 dashboard (5 PH) + tareas operativas de HITL end-to-end (5 PH) = **26 PH**.
**Sprint 4 (operación):** H11 — suite de tests (8 PH) + empaquetado, despliegue y verificación E2E (8 PH) = **16 PH**.

**Total sprints: 94 PH.** (21+31+26+16) — cubre las 81 PH del backlog; la diferencia (13 PH) corresponde a tareas operativas de integración (HITL end-to-end, empaquetado, despliegue y verificación E2E) que no forman parte de las historias.

#### Descomposición en tareas con esfuerzo y dependencias

**Sprint 1 — Fundamentos (21 PH)**

| # | Tarea | Historia | Esfuerzo | Depende de |
|---|-------|----------|----------|------------|
| 1.1 | Definir `AgentIdentity` y `ToolCallEnvelope` (event_id, session_id, digest, size) | H1 | 2 PH | — |
| 1.2 | Separación `execution_arguments`/`telemetry_arguments` + `verify_digest` | H1 | 1 PH | 1.1 |
| 1.3 | `Sanitizer` con regex deterministas (EMAIL, SECRET, PAYMENT_NUMBER, DNI) | H2 | 3 PH | — |
| 1.4 | Truncado de cadenas y límites de profundidad/nodos JSON | H2 | 2 PH | 1.3 |
| 1.5 | `ABACEvaluator` con `allowed_tools` por perfil + fallback in-memory | H3 | 3 PH | 1.1 |
| 1.6 | `DeterministicEvaluator` con precedencia `BLOCK > HITL > PASS` | H3 | 2 PH | 1.5 |
| 1.7 | Guardrails `query_injection`, `topic_relevance`, `rate_limit` | H4 | 5 PH | 1.6 |
| 1.8 | Tests unitarios de envelope, sanitizador, ABAC y guardrails | H4 | 3 PH | 1.2, 1.4, 1.7 |

**Sprint 2 — Enforcement + persistencia (31 PH)**

| # | Tarea | Historia | Esfuerzo | Depende de |
|---|-------|----------|----------|------------|
| 2.1 | `GovernanceMiddleware.pre_hook` (construir envelope, sanitizar, evaluar) | H5 | 5 PH | 1.6, 1.7 |
| 2.2 | `PolicyStore` con carga desde `governance_policies` + caché TTL/vigencia | H5 | 4 PH | 1.5 |
| 2.3 | Migración 002: `policy_decisions`, `execution_results` + índices | H5 | 4 PH | 2.2 |
| 2.4 | `GovernedTool` + hook CrewAI (INV-01/INV-02) | H9 | 3 PH | 2.1 |
| 2.5 | `AuditWriter` persistente (decisiones + resultados) | H7 | 2 PH | 2.3 |
| 2.6 | Migración 003: `audit_chain` + trigger append-only + owner dedicado | H7 | 6 PH | 2.5 |
| 2.7 | Verificador de cadena (`audit/verify.py`) + CLI | H7 | 5 PH | 2.6 |
| 2.8 | Tests H5/H7 (round-trip de decisión, cadena tamper-evident) | H7 | 2 PH | 2.7 |

**Sprint 3 — HITL + consola (26 PH)**

| # | Tarea | Historia | Esfuerzo | Depende de |
|---|-------|----------|----------|------------|
| 3.1 | `InMemoryArgumentHold` idempotente y acotado | H6 | 3 PH | 2.1 |
| 3.2 | `ApprovalService` durable + token opaco (hash) | H6 | 4 PH | 3.1 |
| 3.3 | `ApprovalNotifier` + `ConsoleNotifier` | H6 | 2 PH | 3.2 |
| 3.4 | `GovernedResumeService` (revalidar + consumir + capability 1-uso) | H6 | 4 PH | 3.2, 3.1 |
| 3.5 | `ExecutionOrchestrator` (SUCCESS/FAILED/UNKNOWN) | H6 | 3 PH | 3.4 |
| 3.6 | `ConsoleQueryService` + vista `v_governance_console` (sanitizada) | H8/H10 | 3 PH | 2.3 |
| 3.7 | `ResolutionService` + identidad/evidencia (H8) + vista `v_approval_console` | H8 | 3 PH | 3.5, 3.6 |
| 3.8 | Dashboard Streamlit (KPIs, decisiones, H8, Fase 5) | H10 | 2 PH | 3.7, 2.5 |
| 3.9 | Test HITL end-to-end (predicción MEDIUM → approval → resolución → ejecución) | H6 | 2 PH | 3.7, 3.5 |

**Sprint 4 — Operación (16 PH)**

| # | Tarea | Historia | Esfuerzo | Depende de |
|---|-------|----------|----------|------------|
| 4.1 | Suite unitaria completa + marcador integración | H11 | 3 PH | 2.8, 3.9 |
| 4.2 | Tests de integración de flujos agénticos (varias tool calls por sesión) | H11 | 3 PH | 4.1 |
| 4.3 | Tests de concurrencia (agentes paralelos, idempotencia) + PostgreSQL real | H11 | 2 PH | 4.2 |
| 4.4 | Empaquetado (Containerfile, compose, migraciones explícitas) | — | 3 PH | 4.1 |
| 4.5 | E2E contra stack real (`e2e_fase4`, `e2e_fase5`, `e2e_h8`) | — | 3 PH | 4.4 |
| 4.6 | Verificación de reproducibilidad (suite completa + cadena de auditoría) | — | 2 PH | 4.5 |

**Cierre del hito (prioridad alta pendiente, entregado en este trabajo):**

| # | Tarea | Entregable | Esfuerzo | Depende de |
|---|-------|------------|----------|------------|
| C.1 | Completar criterios de aceptación H1–H4 y H9–H11 en `modulo2-entrega.md` | Documentación | 1 PH | 4.1 |
| C.2 | Verificar HITL end-to-end con el modelo activo (test `test_hitl_end_to_end.py`) | Test de integración | 2 PH | 3.9 |
| C.3 | Test PostgreSQL real (`test_postgres_real.py`: migraciones, constraints, grants, vistas) | Test de integración | 2 PH | 4.3 |
| C.4 | Tests de concurrencia (`test_concurrency.py`: aislamiento, idempotencia, digests) | Test de integración | 2 PH | 4.3 |
| C.5 | Documentar relaciones y cardinalidades completas | Documentación | 1 PH | 4.2 |
| C.6 | Actualizar plan de continuación e hitos de prioridad alta | Documentación | 1 PH | C.1–C.5 |

**Total: 4 sprints (94 PH) + cierre del hito (9 PH) = 103 PH** — cubre el backlog de 81 PH y las tareas operativas de integración, verificación y evidencia.

### 3.4 Aplicación de la Metodología Analítica (CRISP-DM)

1. **Business Understanding:** comprender el proceso de ejecución de agentes financieros y sus riesgos (inyección, fuga de PII, acceso no autorizado, falta de trazabilidad). Definir el objetivo: gobernar cada tool call antes de su efecto.
2. **Data Understanding:** relevar los argumentos que fluyen por las herramientas (`query`, `ticker`, `coin_id`), los perfiles de acceso y los datos sensibles presentes en la telemetría.
3. **Data Preparation:** diseñar el `ToolCallEnvelope` que separa `execution_arguments` de `telemetry_arguments`, calcular digest SHA-256 canónico y tamaño de payload, y sanitizar la telemetría.
4. **Modeling:** implementar el modelo de decisión ABAC (herramientas permitidas por perfil), los guardrails de dominio (inyección, relevancia temática, rate limit) y el policy engine determinista (matching exacto, precedencia `BLOCK > HITL > PASS`, obligaciones de selección).
5. **Evaluation:** validar con tests unitarios e integración que un `BLOCK` aborta la tentativa (INV-01/INV-02), que la sanitización no muta el original (INV-04), que el payload crudo no se persiste (INV-05), que la decisión es determinista (H5) y que la cadena de auditoría detecta manipulación (H7).
6. **Deployment:** integrar el middleware como `GovernedTool` en el Crew de CrewAI, persistir auditoría en PostgreSQL, aplicar migraciones Alembic (001→002→003→004, incluyendo roles de consola H8) y desplegar el dashboard Streamlit para monitoreo en tiempo real.

### 3.5 Estructura de Datos (modelo físico PostgreSQL)

El esquema se construye mediante 4 migraciones Alembic aplicadas en orden.

#### Migración 001 — Schema inicial (R0)

**Tabla `governance_policies`** — políticas ABAC por perfil:
- `policy_id` (PK, String 64), `profile_name`, `tool_name`, `operation_class`, `effect` (ALLOW/DENY), `priority`, `valid_from`, `valid_until`, `created_at`.

**Tabla `governance_decisions`** — decisiones del PEP (R0):
- `decision_id` (PK), `event_id`, `agent_id`, `agent_role`, `tool_name`, `operation_class`, `action` (PASS/BLOCK), `reason_code`, `policy_id` (FK), `payload_digest`, `blocked_by_guardrail`, `latency_pre_ms`, `decided_at`.
- Índices: `ix_decisions_agent_id`, `ix_decisions_decided_at`.

**Tabla `governance_events`** — resultados de ejecución (R0):
- `id` (PK autoincrement), `event_id` (unique), `decision_id` (FK), `tool_name`, `status` (SUCCESS/FAILED), `error_message`, `latency_post_ms`, `recorded_at`.
- Índice: `ix_events_tool_name`.

#### Migración 002 — Policy Engine persistente (H5)

**Ampliación de `governance_events`:** `session_id`, `agent_id`, `agent_role`, `operation_class`, `resource`, `environment`, `payload_digest`, `payload_size_bytes`, `idempotency_key` (unique). Índice `ix_governance_events_recorded_at`.

**Ampliación de `governance_policies`:** clave compuesta `policy_id`/`policy_version` (check `> 0`), `environment`, `resource`, `sensitivity_limit`, `obligations` (JSON), `transform_spec` (JSON), `policy_digest`, `authored_by`. Normalización `effect` a `PASS`/`BLOCK`/`HITL`. Índices `ix_policies_abac`, `ix_policies_validity`.

**Tabla `policy_decisions`** — round-trip H5 completo:
- `decision_id` (PK), `event_id` (unique), `session_id`, `agent_id`, `agent_role`, `tool_name`, `operation_class`, `resource`, `environment`, `payload_digest`, `action` (PASS/BLOCK/HITL), `risk_level` (LOW/MEDIUM/HIGH), `reason_codes` (JSON), `policy_id`, `policy_version`, `policy_digest`, `matched_policy_ids` (JSON), `effective_priority`, `conflicts` (JSON), `obligations` (JSON), `transform_spec` (JSON), `decided_at`, `expires_at`, `blocked_by_guardrail`, `latency_pre_ms`.
- Índices: `ix_policy_decisions_agent_id`, `ix_policy_decisions_action_decided`.

**Tabla `execution_results`** — resultados de ejecución (H5):
- `id` (PK autoincrement), `event_id` (unique, FK→`policy_decisions`), `trim_stats_id`, `decision_id` (FK), `tool_name`, `status`, `error_digest`, `result_digest`, `latency_post_ms`, `recorded_at`.

**Tabla `approval_requests`** — solicitudes HITL (H6/H7 storage):
- `approval_id` (PK), `event_id` (unique, FK), `decision_id`, `agent_id`, `tool_name`, `argument_digest`, `idempotency_key` (unique), `state` (PENDING/APPROVED/REJECTED/EXPIRED/CANCELLED), `argued_at`, `expires_at`, `resolved_at`, `resolved_by`, `decision`.

**Tabla `approval_resume_tokens`** — tokens de reanudación (H6/H7):
- `token_id` (PK), `approval_id` (FK), `token_hash` (unique), `state` (ACTIVE/REDEEMED/REVOKED/EXPIRED), `created_at`, `expires_at`, `redeemed_at`.

**Tabla `audit_chain`** — cadena de auditoría (H7):
- `id` (PK autoincrement), `sequence` (BigInteger unique), `event_id` (FK), `decision_id`, `action`, `chain_hash`, `prev_hash`, `recorded_at`.

**Vista `v_governance_console`** — vista sanitizada (sin raw) que une `policy_decisions` con `execution_results` para dashboard y auditor.

**Roles y grants mínimos:** `argentgob_app` (owner), `argentgob_migrator` (DDL), `argentgob_auditor` (solo lectura), `argentgob_policy_admin` (versiona políticas, no ejecuta tools).

#### Migración 003 — Auditoría encadenada (H7)

- **Columna `payload`** en `audit_chain`: persiste el payload canónico (JSON) para permitir el recálculo del hash.
- **Ownership dedicado:** `audit_chain` pasa a ser propiedad del rol `argentgob_audit_owner` (NOLOGIN); el app deja de ser owner.
- **Trigger append-only:** bloquea `UPDATE`/`DELETE`/`TRUNCATE` sobre `audit_chain`.
- **Grants mínimos:** app solo `INSERT`+`SELECT`; auditor solo `SELECT`; se revoca `UPDATE`/`DELETE`/`TRUNCATE` a los roles de runtime.

> **INV-05:** solo se persisten `telemetry_arguments`/digest, **nunca** el payload crudo.
> **Límite declarado (H7):** sin checkpoint externo no se puede garantizar la detección de una reescritura completa o de un truncado final coherente por parte del owner/superusuario. La cadena es *tamper-evident*, no inmutable.

#### Migración 004 — Roles de consola y resolución autorizada (H8)

- **Vista `v_approval_console`** — vista sanitizada que une approvals con decisiones (estado, tool, `risk_level`, `argument_digest`, edad/expiración) **sin** `execution_arguments` ni raw argument (INV-05).
- **Roles de aplicación:** `console_viewer`, `console_auditor`, `console_approver`, `console_admin`.
- **Grants mínimos:** `console_viewer`/`console_auditor` solo `SELECT` sobre la vista; `console_approver`/`console_admin` además pueden actualizar el estado de las solicitudes HITL. Ningún rol de consola puede leer payloads crudos.

> **H8 — resolución autorizada:** `ResolutionService` exige identidad (rol) y evidencia de autenticación; sin evidencia, la resolución se bloquea (estado `blocked`).

#### Migración 005 — Capa predictiva (Fase 4)

**Tabla `risk_model_versions`** — registro de versiones del modelo:
- PK compuesta `(model_id, model_version)`, `algorithm`, `feature_schema_version`, `training_dataset_version`, `trained_at`, `metrics_json`, `calibration_method`, `decision_thresholds`, `artifact_digest`, `status`.

**Tabla `risk_predictions`** — predicciones por tool call:
- `prediction_id` (PK, `pred-<event_id>`), `event_id`, `decision_id`, `model_id`, `model_version`, `predicted_risk_class` (LOW/MEDIUM/HIGH), `risk_probability`, `class_probabilities` (JSON), `inference_latency_ms`, `feature_schema_version`, `predicted_at`.
- Índices: `ix_risk_predictions_event_id`, `ix_risk_predictions_predicted_at`.

#### Migración 006 — Vista de evidencia (Fase 5)

**Vista `v_risk_evidence`** — cruza `risk_predictions` con `governance_decisions` por `event_id` (17 columnas sanitizadas: identificadores, versión del modelo, clase y probabilidades, latencia de inferencia, decisión final y contexto de la tool call). **No expone** `execution_arguments` ni payload crudo (INV-05).

**Grants:** los roles de consola (`argentgob_console_viewer`, `_auditor`, `_operator`) solo leen la vista, `risk_predictions` y `risk_model_versions`.

> **Trazabilidad:** `corpus_v1` → `risk_model_v1` (`artifact_digest`) → `risk_predictions` → `governance_decisions` → `v_risk_evidence` → dashboard. Ver [0003-arquitectura-y-evidencia.md](analytics/0003-arquitectura-y-evidencia.md).

#### Relaciones y cardinalidades (modelo completo)

| Relación | Cardinalidad | Notas |
|---|---|---|
| `governance_policies` → `policy_decisions` | 1 : 0..N | Una política (versión) puede explicar muchas decisiones. |
| `governance_events` → `policy_decisions` | 1 : 0..1 | Una decisión por evento (`event_id` único). |
| `policy_decisions` → `execution_results` | 1 : 0..1 | Un resultado de ejecución por evento. |
| `policy_decisions` → `risk_predictions` | 1 : 0..1 | Una predicción por evento (`prediction_id = pred-<event_id>`). |
| `risk_model_versions` → `risk_predictions` | 1 : 0..N | Una versión del modelo puede explicar muchas predicciones. |
| `policy_decisions` → `approval_requests` | 1 : 0..N | Solo cuando la decisión es HITL (0..1 en la práctica por evento). |
| `approval_requests` → `approval_resume_tokens` | 1 : 0..N | Una approval puede emitir uno o varios tokens (uno activo por resolución). |
| `approval_requests` → `audit_chain` | 1 : 0..N | Cada resolución/evento puede tener entradas de auditoría. |
| `policy_decisions` → `audit_chain` | 1 : 0..N | La decisión completa se asegura en la cadena. |
| `governance_events` → `audit_chain` | 1 : 0..1 | El evento de ejecución referenciado por la cadena. |
| `governance_decisions` → `risk_predictions` | 1 : 0..1 | La vista `v_risk_evidence` cruza por `event_id` (no por FK). |
| `corpus_v1` (parquet) → `risk_model_versions` | 1 : 1 | `training_dataset_version` referencia el corpus; `artifact_digest` fija el modelo. |

**Invariantes de cardinalidad:**
- `event_id` es único en `policy_decisions`, `approval_requests` y `execution_results` (una tool call = un evento lógico).
- `governance_decisions`/`governance_events` (R0) coexisten con `policy_decisions`/`execution_results` (H5): las primeras son la traza de auditoría R0; las segundas, el round-trip H5 completo. `v_governance_console` une estas fuentes para el dashboard.
- `risk_predictions.prediction_id = 'pred-' + event_id` garantiza 1:1 con la tool call; la FK se vincula al `decision_id` en el upsert (PredictionStore).
- `approval_requests.idempotency_key` es único (`hitl:<event_id>`): la misma tool call no crea approvals duplicadas.

### 3.6 Prototipo de Interfaces de Pantalla (Wireframes)

**Dashboard de monitoreo (Streamlit, `:8501`):**
- **Header:** título "ArgentGob-Mesh · Governance Dashboard" + indicador de auto-refresh (5 s) y nota "Sin raw payload · INV-05".
- **Fila de KPIs (4 tarjetas):** Total decisiones, ✅ PASS (con delta %), ⛔ BLOCK, Latencia pre-hook promedio (ms).
- **Tabla "Decisiones recientes":** `decision_id`, `agent_id`, `agent_role`, `tool_name`, `action` (coloreado verde/rojo), `reason_code`, `payload_digest`, `latency_pre_ms`, `decided_at`.
- **Tabla "Eventos de ejecución":** `event_id`, `decision_id`, `tool_name`, `status`, `error_message`, `recorded_at`.
- **Gráfico "Distribución por herramienta":** bar chart de `tool_name` × `action`.
- **Sección H8 "Consola sanitizada y resolución autorizada":** métricas agregadas, estado de la cadena de auditoría, lista de decisiones sanitizadas con filtros parametrizados (acción/estado/límite), detalle HITL (estado, tool, `risk_level`, `argument_digest`, edad/expiración) y resolución autorizada con roles (`viewer`/`auditor`/`approver`/`admin`) + evidencia de autenticación (modo fixture en desarrollo). Nunca expone `execution_arguments` ni raw payload (INV-05).
- **Sección Fase 5 "Evidencia predictiva":** KPIs de predicciones (distribución LOW/MEDIUM/HIGH, HITL y BLOCK originados por predicción, tasa HITL predictiva, latencia media de inferencia), tabla "Predicción vs decisión final" con motivo de divergencia, agregado de divergencias, distribución temporal de riesgos, versiones del modelo registradas y evaluación del modelo (matriz de confusión, métricas por clase y curva de calibración sobre la partición de evaluación congelada).

**Rol en el proceso de negocio:** permite al operador de seguridad observar en tiempo real qué decisiones toma el PEP, detectar bloqueos recurrentes (inyección, rate limit), correlacionar decisiones con resultados de ejecución y resolver solicitudes HITL con identidad y evidencia, sin exponer datos sensibles.

### 3.7 Diagrama de Arquitectura

```mermaid
flowchart LR
    subgraph Agente
        A[Agente CrewAI<br/>LLM local llama-3.1-8b]
        T[GovernedTool<br/>_run]
    end

    subgraph Middleware de Gobernanza
        PEP[Modulo A - PEP<br/>GovernanceMiddleware]
        B[Modulo B - Sanitizer<br/>PII + truncado]
        C[Modulo C - ABAC + Guardrails<br/>+ Policy Engine H5]
        HITL[Modulo HITL - H6<br/>Aprobacion humana]
    end

    subgraph Persistencia y UI
            DB[(PostgreSQL<br/>policies / decisions<br/>events / approvals<br/>audit_chain H7<br/>v_approval_console H8)]
            DASH[Dashboard Streamlit<br/>:8501 + consola H8]
        VERIF[Verificador audit CLI<br/>H7]
    end

    A -->|tool call| T
    T -->|pre_hook| PEP
    PEP --> B
    PEP --> C
    C -->|PASS/BLOCK/HITL| PEP
    PEP -->|HITL| HITL
    HITL -->|approval + token| DB
    PEP -->|HookAborted si BLOCK| T
    PEP -->|decision + resultado| DB
    DB --> DASH
    DB --> VERIF
        DASH -->|resolucion autorizada H8| HITL
    ```

    **Flujo de comunicación:**
    1. El agente CrewAI invoca una herramienta → `GovernedTool._run`.
    2. `pre_hook` construye el envelope, sanitiza (Módulo B), ejecuta la inferencia predictiva (Feature Builder + Risk Model) y evalúa ABAC + guardrails + policy engine (Módulo C).
    3. Si `BLOCK` (determinista o por predicción HIGH) → se lanza `HookAborted` y la herramienta **no se ejecuta** (INV-01/INV-02).
    4. Si `HITL` (determinista o por predicción MEDIUM) → se crea una solicitud de aprobación durable y se emite un token de reanudación (H6).
    5. Si `PASS` → se ejecuta la herramienta y `post_hook` registra el resultado.
    6. Decisiones, eventos y entradas de la cadena de auditoría se persisten en PostgreSQL; el dashboard y la consola H8 los consumen para monitoreo en tiempo real y el verificador CLI valida la integridad de la cadena.
    7. La consola H8 permite resolver solicitudes HITL con identidad y evidencia de autenticación (nunca expone el payload crudo, INV-05).

---

## 4. Invariantes clave del prototipo

- **INV-01** — la herramienta nunca se ejecuta antes de una decisión válida.
- **INV-02** — un veredicto `BLOCK` aborta la tentativa (sin efecto secundario).
- **INV-04 / INV-05** — el payload crudo nunca se reutiliza ni se persiste; solo se almacena telemetría sanitizada y el digest SHA-256 (incluye la consola H8: ninguna respuesta expone `execution_arguments` ni raw argument).
- **INV-11** — ante duda, la política falla cerrada (`BLOCK`).
- **INV-15** — los enums desconocidos fallan de forma segura.
- **INV-16** — el detector produce metadata/finding sin valor y no muta `execution_arguments`.

---

## 5. Cómo reproducir la verificación

```bash
# Levantar el stack
make start

# Ejecutar la suite de pruebas dentro del contenedor actualizado
podman run --rm -w /app localhost/argentgob-mvp-agent:latest python -m pytest tests/ -q

# Verificar la integridad de la cadena de auditoría
podman run --rm --network argentgob-mvp_default \
  -e DATABASE_URL="postgresql+psycopg://argentgob_app:dev-password-local@postgres:5432/argentgob" \
  localhost/argentgob-mvp-agent:latest python -m argentgob.audit.cli

  # Verificar la consola H8 contra la DB real (23/23 checks)
  podman run --rm --network argentgob-mvp_default \
    -v "${PWD}:/app:Z" -w /app \
    localhost/argentgob-mvp-agent:latest python scripts/e2e_h8_console.py

  # Verificar la capa predictiva contra la DB real (6/6 checks)
  podman run --rm --network argentgob-mvp_default \
    -v "${PWD}:/app:Z" -w /app \
    localhost/argentgob-mvp-agent:latest python scripts/e2e_fase4_predictive.py

  # Verificar la evidencia académica contra la DB real (19/19 checks)
  podman run --rm --network argentgob-mvp_default \
    -v "${PWD}:/app:Z" -w /app \
    localhost/argentgob-mvp-agent:latest python scripts/e2e_fase5_evidence.py

  # Dashboard
  make dashboard   # http://localhost:8501
```
