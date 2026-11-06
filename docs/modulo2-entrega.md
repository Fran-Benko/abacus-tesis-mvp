# Entrega Módulo 2 — Prototipo Tecnológico ArgentGob-Mesh

> **Proyecto de tesis.** Toda la documentación está en español.
> Este documento describe el prototipo tecnológico **ArgentGob-Mesh**: un middleware de gobernanza perimetral para agentes CrewAI en el dominio financiero.

---

## 0. Estado verificado del prototipo

El contenido de este documento refleja el estado **real y verificado** del prototipo, ejecutado dentro de los contenedores Podman con las imágenes actualizadas (`localhost/argentgob-mvp-agent:latest`).

| Verificación | Resultado |
|---|---|
| Suite de pruebas completa (`pytest tests/`) | ✅ **203 passed** (192 unit + 11 integración) |
| Dashboard Streamlit (`:8501`) | ✅ HTTP 200 |
| LLM local llama-server (`:8080`) | ✅ HTTP 200 |
| PostgreSQL (`argentgob-mvp-postgres-1`) | ✅ Healthy |
| Cadena de auditoría (`audit_chain`) | ✅ **Íntegra** (verificador CLI: `OK`) |
| E2E consola H8 (`scripts/e2e_h8_console.py`) | ✅ **23/23 checks OK** |
| Migraciones aplicadas | ✅ 001 → 002 → 003 → 004 |

**Datos reales en la base (al momento de la verificación):**

- `policy_decisions`: 1 decisión `PASS`.
- `audit_chain`: 1 entrada encadenada (génesis).
- `governance_policies`: 6 políticas `PASS` (admin: 1, analyst: 3, analyst_restricted: 2).
- `approval_requests`: sin registros (flujo HITL sin activar en esta corrida).

> **Nota de trazabilidad:** el worktree de la rama `feat/r0-reconciliation-baseline` contiene solo la migración `001` y el alcance R0 (Módulos A/B/C + auditoría básica). Las funcionalidades **H5 (policy engine persistente), H6 (aprobación humana HITL), H7 (auditoría encadenada) y H8 (consola sanitizada y resolución autorizada)** están implementadas y verificadas en las imágenes de contenedor actualizadas (`argentgob-mvp-agent`), con migraciones `002`, `003` y `004` aplicadas en la base. Este documento cubre el alcance completo del prototipo tal como corre en los contenedores.

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

### 3.3 Sprint Backlog (sprints iniciales)

**Sprint 1 (fundamentos):** H1, H2, H3, H4 — envelope, sanitizador, ABAC y guardrails (3+5+5+8 = **21 PH**).
**Sprint 2 (enforcement + persistencia):** H5, H7, H9 — policy engine persistente, auditoría encadenada e integración CrewAI (13+13+5 = **31 PH**).
**Sprint 3 (HITL + consola):** H6 — aprobación humana (13 PH) + H8 consola sanitizada (3 PH) + H10 dashboard (5 PH) + tareas operativas de HITL end-to-end (5 PH) = **26 PH**.
**Sprint 4 (operación):** H11 — suite de tests (8 PH) + empaquetado, despliegue y verificación E2E (8 PH) = **16 PH**.

**Total sprints: 94 PH.** (21+31+26+16) — cubre las 81 PH del backlog; la diferencia (13 PH) corresponde a tareas operativas de integración (HITL end-to-end, empaquetado, despliegue y verificación E2E) que no forman parte de las historias.

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

### 3.6 Prototipo de Interfaces de Pantalla (Wireframes)

**Dashboard de monitoreo (Streamlit, `:8501`):**
- **Header:** título "ArgentGob-Mesh · Governance Dashboard" + indicador de auto-refresh (5 s) y nota "Sin raw payload · INV-05".
- **Fila de KPIs (4 tarjetas):** Total decisiones, ✅ PASS (con delta %), ⛔ BLOCK, Latencia pre-hook promedio (ms).
- **Tabla "Decisiones recientes":** `decision_id`, `agent_id`, `agent_role`, `tool_name`, `action` (coloreado verde/rojo), `reason_code`, `payload_digest`, `latency_pre_ms`, `decided_at`.
- **Tabla "Eventos de ejecución":** `event_id`, `decision_id`, `tool_name`, `status`, `error_message`, `recorded_at`.
- **Gráfico "Distribución por herramienta":** bar chart de `tool_name` × `action`.
- **Sección H8 "Consola sanitizada y resolución autorizada":** métricas agregadas, estado de la cadena de auditoría, lista de decisiones sanitizadas con filtros parametrizados (acción/estado/límite), detalle HITL (estado, tool, `risk_level`, `argument_digest`, edad/expiración) y resolución autorizada con roles (`viewer`/`auditor`/`approver`/`admin`) + evidencia de autenticación (modo fixture en desarrollo). Nunca expone `execution_arguments` ni raw payload (INV-05).

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
    2. `pre_hook` construye el envelope, sanitiza (Módulo B) y evalúa ABAC + guardrails + policy engine (Módulo C).
    3. Si `BLOCK` → se lanza `HookAborted` y la herramienta **no se ejecuta** (INV-01/INV-02).
    4. Si `HITL` → se crea una solicitud de aprobación durable y se emite un token de reanudación (H6).
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

  # Dashboard
  make dashboard   # http://localhost:8501
```