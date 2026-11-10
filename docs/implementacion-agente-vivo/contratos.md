# Contratos, flujos e invariantes — v3

**Objetivo, no implementación actual.** Autoridad y alcance: [índice](README.md). Configuraciones: [despliegue](despliegue.md). Casos y permisos: [catálogo](escenarios.md).

## 1. Invariantes obligatorias

| ID | Regla | Prueba |
|---|---|---|
| INV-01/02 | No executor sin decisión válida para esa tentativa; BLOCK y HITL pendientes no ejecutan. | T-B04, T-H01 |
| INV-04/05 | Argumentos operativos solo en memoria del ejecutor autorizado; nunca en persistencia, logs, buffer, API de lectura o UI. Telemetría no se reutiliza para ejecutar. | T-P01…T-P06 |
| INV-11 | Identidad, política, transformación o auditoría requerida inválidas → no ejecutar; no fallback permisivo. | T-B03, T-A06, T-H06 |
| INV-16 | Detector produce findings sin valor, no muta `execution_arguments`. | T-P03 |
| AV-I01 | BLOCK > HITL > PASS; aprobación no anula un control duro. | T-H06, T-R03 |
| AV-I02 | Lectura autorizada y liberación de contenido son controles distintos. Un bloqueo de resultado no reescribe el PASS previo. | T-I02 |
| AV-I03 | LLM real requiere llamada demostrable al servidor local; doble de prueba nunca se rotula como inferencia real. | T-R05 |
| AV-I04 | Retry de lectura permitida sigue las reglas existentes; ninguna acción de efecto se reejecuta automáticamente tras timeout/resume/reinicio. | T-B08, T-H03 |
| AV-I05 | Actor humano, identidad de servicio y perfil del agente son conceptos distintos. `admin` de debug queda fuera de UI/API de runs. | T-B03, T-H02 |
| AV-I06 | Nunca exponer razonamiento interno, prompts, token opaco ni stdout libre como progreso del agente. | T-B06, T-P05 |

Los IDs AV son locales; no renumerar las invariantes históricas.

## 2. Flujos e información permitida

### Flujo ordinario

1. Dashboard obtiene identidad de operador con el mecanismo H8 y genera clave idempotente por intención de ejecución.
2. API autentica servicio y actor, valida escenario/perfil/input y límites **antes del primer prompt**. Entrada inválida: cero inferencias y cero tools.
3. Reserva atómica del cupo, crea `run_id` y proceso hijo con contexto independiente.
4. Builder selecciona tools/políticas registradas y conecta CrewAI al llama.cpp real. No aceptar clases, recursos, URLs o ejecutores suministrados por el cliente.
5. Cada tentativa atraviesa el PEP existente, emite evento final de decisión y solo ejecuta si corresponde.
6. Resultado de proveedor pasa proyección PII/control IPI antes de entrar en el contexto del LLM. Si se retiene, entregar solo aviso seguro.
7. Resultado estructurado del Crew se valida/proyecta antes de publicarse. Datos sin evidencia o citas inválidas no se convierten en hechos.
8. UI consulta snapshot y eventos por cursor; auditoría persistida sigue siendo evidencia, no el buffer volátil.

### Cuatro planos PII

| Plano | Input operativo | Output permitido |
|---|---|---|
| Usuario → API/LLM | Referencia sintética + solicitud breve | Texto validado sin identificadores personales; rechazo seguro si está fuera de cobertura |
| PEP → proveedor | Identificador de fixture autorizado y argumentos efectivos | Argumentos operativos necesarios, nunca derivados de telemetría |
| Proveedor → LLM | Fixture con campos sintéticos sensibles | Proyección por allowlist; categorías mínimas necesarias y referencias opacas |
| LLM → API/UI/DB | Respuesta estructurada no confiable | Campos permitidos, texto saneado, referencias validadas y metadata |

DNI/CUIL/CUIT se detectan en representaciones string/numéricas según esquema. Domicilio, ingresos y situación previsional se excluyen por rutas tipadas, no por promesa de regex semántico. Validar listas, objetos anidados, Unicode, claves desconocidas, tamaño y timeout del scanner. No mostrar comparación raw/redactado en pantalla. Los canarios sintéticos usados para no-leak no se agregan a logs de tests.

`USE_TRANSFORMED_ARGUMENTS` no implica masking. Para una transformación nueva: función/version conocidas, allowlist de campos modificables, copia nueva, tamaño máximo, input/output digest y validación posterior. Si altera otros campos o queda inválida, bloquear sin fallback al original. Registrar solo metadata. No construir una segunda ruta de selección duplicada.

### IPI y control de resultados

El control inicial acepta documentos de texto controlados, no adjuntos PDF/OCR. Limitar tamaño y normalizar representación antes del detector. Documento se trata como evidencia de terceros, nunca como mensaje de sistema. Patrones detectan casos definidos, no toda inyección posible.

Una lectura PASS puede terminar `execution_status=SUCCESS` y `content_status=WITHHELD`. El evento de liberación lleva `control_action=BLOCK` y motivo, **sin inventar una segunda ejecución ni alterar la decisión anterior**. Reutilizar persistencia existente si soporta esta distinción; si no, AV-00/04 debe acordar una ampliación mínima y probar migración antes de cerrar. Un badge sin evidencia durable no satisface el gate.

## 3. Contrato HTTP propuesto

Ruta interna, JSON estricto; FastAPI/ASGI con un worker. No asumir que estas dependencias directas o endpoints ya existen.

### 3.1 Estado implementado en AV-01 (2026-11-10)

`src/argentgob/live/api.py` implementa `GET /health`, `GET /ready`, `POST /runs` y `GET /runs/{id}`. `POST /runs/{id}/resume` **no** existe todavía (AV-05). El escenario habilitado es solo `finanzas`, con perfiles `analyst` y `analyst_restricted`; `admin` se rechaza con 422 (AV-I05). El actor llega por la cabecera `x-argentgob-actor` y el token de servicio por `x-argentgob-service-token`; con `AGENT_API_SERVICE_TOKEN` configurado, la ausencia o el valor incorrecto produce 401, y fuera de LOCAL/TEST sin token configurado se responde 503 (fail-closed). El cuerpo se limita a 65536 bytes antes del parseo (413).

`/ready` separa `checks` (configuración mínima del servicio) de `capabilities` (`llm_base_configured`, `llm_reachable`, `predictor_artifacts_present`, `capacity_free`), de modo que un cupo ocupado no se confunda con un proceso roto. **Desde AV-02** `checks` incluye `db_reachable`, `schema_en_head` y `catalogo_sembrado`: un inicializador fallido deja `ready=false` y `POST /runs` responde 503; sin LLM alcanzable también responde 503, aunque `/health`, `/ready` y el dashboard siguen operativos. El supervisor mantiene un run activo global, idempotencia por `(actor, idempotency_key)` con digest canónico del input, TTL de runs terminales y tombstones acotados. Un reinicio del servicio pierde runs y eventos: la UI debe explicar la pérdida sin simular recovery.

**Desde AV-03** el dashboard consume esta API con `argentgob.dashboard.live_client` (token de servicio montado, actor por cabecera, errores traducidos a mensajes seguros sin eco de payload) y `argentgob.dashboard.live_session` (clave de idempotencia determinista por intención, cursor incremental, polling detenido en estado terminal). La vista `pages/live_finance.py` admite un run solo por intención explícita y sondea la región activa en `st.fragment(run_every="2s")`; no reenvía el `POST` en rerun ni en doble clic, no aprueba HITL y no muestra consola raw. La visibilidad incremental medida en el gate fue 0.01 s con el run en `RUNNING`.

### POST /runs

```json
{
  "scenario_id": "tramites",
  "profile": "tramites_agent",
  "input": {"case_id": "TRAMITE-DEMO-01", "request": "Clasificar la solicitud y señalar el siguiente paso"},
  "idempotency_key": "intencion-local-001"
}
```

- `scenario_id`: catálogo cerrado. `profile`: permitido para actor y escenario; no libremente escalable.
- `input`: discriminado por escenario, campos extra rechazados. Esquemas exactos en [catálogo](escenarios.md).
- Actor no se toma del body: evidencia de identidad H8 verificada por el servidor. Credencial dashboard→API no autoriza por sí sola resolver HITL.
- Clave 1–128 caracteres permitidos; huella canónica del input en memoria, retención igual al run. Misma clave/input/actor devuelve mismo run; cambio de input produce 409. No garantía después del TTL/reinicio.
- 202 inicial: `run_id`, `state=STARTING`, `poll_after_ms`, ruta de consulta. Repetición válida devuelve el recurso existente, sin nuevo proceso.

### GET /runs/{id}?after_seq=N&limit=M

`N ≥ 0`, `1 ≤ M ≤ 100`; actor solo accede a runs autorizados.

```json
{
  "schema_version": "1",
  "run_id": "uuid-generado-por-servidor",
  "state": "RUNNING",
  "events": [],
  "next_seq": 0,
  "first_available_seq": 1,
  "truncated": false,
  "result": null,
  "predictor_status": "NOT_VALIDATED_FOR_DOMAIN"
}
```

Estado/snapshot siempre completo, aunque falten eventos del anillo. `result` solo tras validación de salida. No reutilizar input como título ni incluir cuerpos/errores raw. Si dos actores no están autorizados entre sí, respuesta 404 uniforme para no revelar existencia.

### Otras rutas

- `GET /health`: proceso HTTP vivo, 200 sin consultar repetidamente el LLM.
- `GET /ready`: 200 si dependencias/configuración permiten trabajar, 503 en otro caso; capacidades por escenario y ocupación se informan separadamente. Cupo ocupado no equivale a proceso roto.
- `POST /runs/{id}/resume`: solicita ejecutar la acción retenida; recibe referencia de aprobación/token_id y prueba opaca por canal autenticado, nunca argumentos nuevos ni ejecutor. Verificar actor y asociación exacta. Sin autoaprobación.
- Resolución aprobar/rechazar sigue el servicio H8 existente; no duplicar autorización en una API paralela.

Errores: 413 tamaño; 422 esquema; 401 autenticación; 403 permisos generales; 404 recurso no visible; 409 cupo/conflicto/estado; 503 dependencias; 410 run expirado conocido durante ventana de tombstone. Tras reinicio, IDs perdidos pueden dar 404; la UI explica pérdida de sesión sin simular recovery.

## 4. Eventos y estados

Campos obligatorios: `schema_version`, `run_id`, `seq` creciente, timestamp UTC, `kind`. Campos opcionales por allowlist: `event_id` (tentativa), `decision_id`, herramienta, recurso lógico, decisión, `reason_codes`, estado de ejecución, estado de contenido, latencia medida, referencia de aprobación y digest necesario. `null`/ausente significa no disponible; no inventar latencias o riesgo.

### 4.1 Estado implementado en AV-01 (2026-11-10)

`src/argentgob/live/events.py` implementa la allowlist (`_ALLOWED_FIELDS`), el catálogo cerrado de `kind` y un anillo acotado (`max_events`, `max_event_bytes`) con `seq` monotónica, `first_available_seq` y `truncated`. Un `kind` fuera del catálogo se descarta; un campo fuera de la allowlist no se publica. `sanitize_payload` recorta ANSI/controles y, si el evento excede el tamaño, descarta `result_summary` y luego reduce a metadata mínima marcando `truncated`.

El PEP emite por un `GovernanceObserver` inyectado (`src/argentgob/core/observer.py`): `tool.requested` al construir el envelope, `governance.decided` tras persistir la decisión (incluye `blocked_by` como `message`), `tool.started` antes del executor, `tool.completed`/`tool.failed` en el post-hook y `approval.pending` al crear la aprobación H6. Sin observador se usa `NullObserver`, que conserva el comportamiento de CLI y tests. Una excepción del observador se registra y **no** altera la decisión de negocio.

El canal IPC es un socket loopback efímero con handshake de token de un solo uso (`SocketIpc`); el hijo (`python -m argentgob.live.child`) escribe JSON-lines y el padre lo consume en un hilo lector. `stdout`/`stderr` del hijo van a `DEVNULL` y nunca se convierten en eventos.

Agregación implementada (`_DecisionTally`): `SUCCEEDED` solo si todas las tentativas fueron PASS y hay resultado válido; con acciones retenidas (HITL) o bloqueadas y resultado → `PARTIAL`; sin ninguna ejecución autorizada → `BLOCKED`. Un `HookAborted` sin resultado también cierra `BLOCKED`. digest necesario. `null`/ausente significa no disponible; no inventar latencias o riesgo.

Tipos: `run.accepted`, `run.started`, `tool.requested`, `governance.decided`, `tool.started`, `tool.completed`, `tool.failed`, `content.released`, `content.withheld`, `approval.pending`, `approval.resolved`, `execution.resumed`, `run.finished`.

El callback se invoca explícitamente en fronteras verificables, no parseando logs globales. Callback no-op conserva CLI/tests. Protocolo IPC de esquema cerrado, mensajes/cola acotados; stdout no se transforma en eventos. Si observación falla, señalar degradación y conservar auditoría obligatoria: no alterar arbitrariamente la decisión de negocio.

Estados terminales: `SUCCEEDED`, `PARTIAL`, `BLOCKED`, `FAILED`, `TIMED_OUT`, `REJECTED`, `EXPIRED`, `INDETERMINATE`. Transiciones:

- `STARTING → RUNNING → terminal`.
- `RUNNING → WAITING_APPROVAL → RESUMING → terminal`.
- `WAITING_APPROVAL → REJECTED | EXPIRED | FAILED` sin ejecutar acción retenida.
- Timeout con efecto desconocido termina `INDETERMINATE`, no `FAILED` conocido; nunca reintentar automáticamente.

Regla de agregación: `SUCCEEDED` solo si la tarea requerida tiene evidencia/salida válida y ninguna acción necesaria quedó retenida/fallida. Un informe con evidencia parcial es `PARTIAL`; si ninguna operación requerida pudo ejecutarse por gobernanza, `BLOCKED`. Un rechazo de input es HTTP error sin run. La abstención jurídica válida es salida de dominio `INSUFFICIENT_EVIDENCE`, no error técnico. Decisión de una tool, ejecución y estado del run permanecen separados.

Un run ocupa el cupo también en WAITING_APPROVAL. Cada run tiene contador, middleware y hold propios. Terminar cooperativamente y confirmar muerte del proceso hijo antes de liberar cupo. Timeout de hilo no prueba cancelación.

## 5. HITL y revalidación

1. PEP persiste decisión HITL y crea aprobación/hold; ningún executor se invoca.
2. Proceso propietario retiene contexto acotado y espera con TTL. API del padre continúa respondiendo.
3. Operador autorizado resuelve mediante H8. Token opaco no aparece en eventos, URL, DB raw ni historial UI.
4. Solicitud de resume llega al proceso propietario por control IPC. Revalidar estado de aprobación, expiración, digest, política vigente/version compatible y guardrails duros **antes de consumir/ejecutar**.
5. Reutilizar `GovernedResumeService` y `ExecutionOrchestrator`; verificar lo que el código realmente valida. El docstring actual de resume describe controles que no deben darse por demostrados sin pruebas.
6. Claim único y executor registrado en servidor; dos resumes concurrentes producen como máximo una invocación.
7. Resultado se persiste y proyecta. No repetir `crew.kickoff()` ni afirmar continuidad del razonamiento original. Mostrar resultado operativo post-aprobación como etapa separada.
8. Tras reinicio/hold vencido, no ejecutar ni reconstruir argumentos desde logs. Exponer indisponibilidad y requerir una nueva intención explícita.

En la ruta actual, HITL puede elevarse antes de recorrer guardrails. AV-05 debe demostrar que controles duros no se omiten tras aprobación, con ajuste mínimo de esa ruta y regresión de precedencia. Un token válido no autoriza un payload que ahora debe bloquearse.

### 5.1 Estado verificado en AV-00 (2026-11-10)

`GovernedResumeService._revalidate` valida **solo** `approval.state == APPROVED` y las expiraciones de approval y token. No revalida política vigente, `policy_version`/`policy_digest` ni guardrails, pese a lo que afirma su docstring. El `argument_digest` se contrasta en `_require_hold` únicamente entre el hold y la aprobación, no contra la decisión persistida.

Consecuencia verificada: una aprobación válida permite ejecutar aunque la política haya cambiado o un control duro deba bloquear. AV-05 debe implementar la revalidación efectiva **antes** de consumir el token y probarla cambiando la política entre aprobación y resume (T-H06). El docstring no es evidencia.

Además, `AuditChainAppender` no está cableado al runtime: solo lo instancian tests. En la DB del stack hay 74 `policy_decisions` y 1 fila en `audit_chain` (de un E2E H7 manual), por lo que `audit.cli` no acredita que un run real quede encadenado. AV-05 debe integrar el append en la frontera de decisión (T-D01).

## 6. Salidas por dominio

Sobre común seguro: `summary`, `evidence_refs`, `limitations`, `domain_status`. La lista de campos se cierra por escenario; no aceptar objetos arbitrarios generados por el LLM. Validador comprueba referencias contra lo realmente recuperado y no persiste salida no validada.

En jurídico y auditoría, validación estructural/citas no garantiza corrección semántica total: documentar revisión humana y límites. Los buffers tienen TTL y cupos definidos en [despliegue](despliegue.md). Los controles verificables están en [pruebas](verificacion.md).
