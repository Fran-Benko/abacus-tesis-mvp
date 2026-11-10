# Configuración y despliegue objetivo — v3

**Propuesta para AV-01/02; no describe servicios nuevos ya desplegados.** [Índice](README.md), [contratos](contratos.md), [gates](verificacion.md).

> **Estado AV-03 (2026-11-10):** el dashboard monta `service_token` y exporta `AGENT_API_SERVICE_TOKEN_FILE`, de modo que la vista Finanzas en vivo consume la API interna con autenticación de servicio. Evidencia en [sesión 0028](../sessions/0028-resumen-av-03-ui-financiera-en-vivo.md).
>
> **Estado AV-02 (2026-11-10):** `compose.yaml` declara `agent` como API persistente (`python -m argentgob.live.server`, puerto interno 8090, sin publicación al host ni stdin/tty) y `migrator` como inicializador obligatorio (`python -m argentgob.db.init`) con `service_completed_successfully`. Los secretos se montan por consumidor y el DSN se construye con escape (`argentgob.db.dsn`). Evidencia en [sesión 0027](../sessions/0027-resumen-av-02-compose-inicializacion.md).

## 1. Topología

| Servicio | Arranque | Ready / salud | Exposición |
|---|---|---|---|
| postgres | Persistente, volumen actual preservado | `pg_isready` y conexión autenticada del consumidor | Solo red bridge |
| migrator | Run-once tras DB saludable | Alembic + seed/validación; exit 0, no healthcheck periódico | Sin puerto |
| llama-server | Persistente, GGUF RO y GPU/CDI | `/health`, tolerando carga inicial | Interno; loopback 8080 opcional para diagnóstico |
| agent | API persistente, un proceso ASGI y un hijo por run | `/health` y `/ready` diferentes | Solo red interna, 8090 |
| dashboard | Persistente tras inicialización DB | `/_stcore/health` | Loopback 8501 |

Agente y dashboard esperan migrator completado correctamente. Agente puede arrancar sin LLM disponible para exponer diagnóstico, pero no admite runs que lo requieran. Dashboard no depende del LLM ni del agente para mostrar auditoría/demo determinista. Dependencias durante ejecución se comprueban con timeouts, no solo `depends_on`.

Verificar condiciones con el proveedor Compose instalado. Si no soporta `service_completed_successfully`, entrypoint acotado verifica esquema/catálogo antes de servir/admitir; nunca confiar solo en sleeps. Conservar red default del proyecto, nombre y volumen; no declarar red externa ni crear volumen distinto por accidente. Healthcheck no significa reinicio automático por unhealthy.

## 2. Configuración por servicio

Los nombres nuevos de esta tabla son **propuestos**: implementarlos/validarlos en Settings o entrypoint antes de usarlos en compose; variables hoy ignoradas por Settings no configuran nada por sí solas.

| Parámetro | Consumidor | Default objetivo / restricción |
|---|---|---|
| `OPENAI_API_BASE` | agent | `http://llama-server:8080/v1`, cerrado por operador, no por usuario |
| `OPENAI_MODEL_NAME`, clave local | agent | Mantener configuración financiera probada; no function calling nativo sin nuevo smoke |
| `AGENT_API_BASE` | dashboard | **Implementado en AV-03**: `http://agent:8090`, configuración interna; la vista Finanzas en vivo lo consume |
| `AGENT_API_HOST`, `AGENT_API_PORT` | agent | `0.0.0.0` y `8090`; implementados en `argentgob.live.server` (AV-01) |
| `AGENT_API_SERVICE_TOKEN` / `_FILE` | agent | Token por cabecera `x-argentgob-service-token`; **AV-02** lo monta como `.secrets/service_token` vía `AGENT_API_SERVICE_TOKEN_FILE`. Ausente fuera de LOCAL/TEST → 503 fail-closed |
| Credencial servicio API | dashboard/agent | **Implementado en AV-03**: el dashboard monta `.secrets/service_token` y lo envía por cabecera; sin default público, sin eco ni query string |
| Identidad/roles H8 | dashboard/agent | **Implementado en AV-02**: `IDENTITY_FIXTURE_ROLES` y `AUTHORIZED_AUDITOR_ROLES` declarados en `compose.yaml`; fixture solo LOCAL/TEST, explícitamente no autenticación productiva |
| DB secret canónico | postgres/migrator/agent/dashboard | **Implementado en AV-02**: `.secrets/{postgres,runtime,dashboard}_password` montados por servicio; `resolve_database_url()` construye el DSN con `quote` y el archivo gana sobre una URL heredada |
| `ENVIRONMENT`, `LOG_LEVEL` | consumidores pertinentes | LOCAL/TEST, INFO sin verbose raw; no anchor universal con secretos de más |
| `RISK_MODEL_DIR` | agent/dashboard | **Implementado en AV-02**: `/app/data/models/risk_model_v1` dentro de la imagen (RO); `RISK_CORPUS_PATH` análogo. Nunca se acepta un path suministrado por el cliente |
| Directorio fixtures nuevo | agent | `data/demo_publica/v1`, RO; catálogo cerrado/versionado |
| GGUF path / imagen | llama-server | **Implementado en AV-02**: imagen fijada por digest `sha256:6ac9215…`, `GGUF_DIR`/`GGUF_FILE` parametrizados y montaje `:ro` |
| `MAX_PAYLOAD_BYTES` existente | agent | 65536 como techo de admisión HTTP/envelope, límite previo al parseo/scan caro |
| `MAX_JSON_DEPTH`, nodos, strings | agent | Límites existentes: 16 / 1000 / 4096 bytes por string; validar aplicación efectiva |
| Límite tools | hijo por run | Por perfil de catálogo; no confundir con `max_iter` |
| Timeout activo nuevo | supervisor | 180 s inicial por run, configurable tras medición; espera HITL separada |
| Timeout LLM/proveedor nuevo | agent | 60 s por petición LLM y 15 s por petición de fixture/proveedor como valores iniciales; no exceder presupuesto total |
| Espera HITL/hold | hijo / servicios H6 | 300 s inicial, hold TTL ≤ aprobación TTL y límites H6 existentes |
| Límite ejecución/salida LLM | agent | Preservar 512 tokens financieros al comenzar; definir presupuesto por dominio y no truncar silenciosamente JSON inválido |
| Eventos / tamaño | supervisor | 256 eventos, 2 KiB por evento; cola IPC máxima 256 mensajes con política de degradación explícita |
| Runs terminales / TTL | supervisor | Máximo 10 / 15 min; cupo activo separado; también acotar idempotencia/tombstones al mismo máximo/TTL |
| Resultado seguro | supervisor | Máximo 32 KiB por run; salida más grande se rechaza/limita con estado visible, no raw |
| Polling | dashboard | 1–2 s mientras activo, cero polling automático al terminar |

Timeouts son defaults de diseño, no performance ya medida. Si una tarea requiere más, cambiar configuración y expectativa antes del gate, documentando el motivo. No elevar límites para ocultar bucles de tools.

## 3. Inicialización y artefactos

1. Preflight valida proveedor/versión Podman Compose, GPU/CDI, GGUF legible, secretos presentes (sin valores), artefactos predictivos y puertos.
2. PostgreSQL saludable → migrator ejecuta upgrade y seed canónico validado. Si falla cualquiera, exit distinto de cero y no admitir runs.
3. Seed idempotente y acotado al catálogo administrado; revisar pruning por environment antes de automatizarlo. No borrar políticas ajenas ni sobreescribir versiones usadas con semántica nueva.
4. Agente valida esquema, políticas/guardrails registrados, modelo financiero y fixtures. Públicos no usan predictor fuera de dominio; falta de modelo financiero no se oculta como predictor funcionando.
5. Montar artefactos del predictor en agente, RO; hoy el Containerfile no copia `data/`. GGUF solo en llama-server; corpus para evidencias solo donde haga falta.
6. Probar una imagen sin bind mount de fuentes para detectar archivos faltantes. Un test con `:/app` no verifica el empaquetado.
7. No instalar dependencias para esta documentación. AV-01 declarará FastAPI/ASGI directamente si se usa esa implementación y probará la imagen reconstruida. No actualizar CrewAI/Streamlit de forma incidental.

## 4. Salud y degradación

| Falla | Comportamiento obligatorio |
|---|---|
| DB caída antes del efecto | Rechazar run/acción; sin ejecutar ni emitir éxito ficticio |
| Auditoría requerida no persistible | Fail-closed antes del efecto; si ya pudo ocurrir, diagnóstico indeterminado y sin retry |
| LLM ausente o GGUF faltante | Readiness informa dependencia; 503 al iniciar; auditoría/dashboard y demo sintética continúan |
| Predictor ausente | Finanzas no se anuncia como predictivo operativo; público determinista rotulado, sin inferencia fuera de dominio |
| Migración/seed fallidos | Inicialización fallida, no admitir trabajo; log seguro y no reinicios sin límite del inicializador |
| Worker atascado | Parada cooperativa, luego terminación supervisada; no liberar cupo hasta confirmar salida |
| Servicio reiniciado / hold perdido | Sesión operativa perdida; aprobaciones auditables pero no reanudables sin argumentos; ninguna reconstrucción raw |
| Buffer lleno/observador caído | Snapshot y `truncated`/degradación; nunca inventar eventos ni sustituir auditoría durable |

## 5. Validación aislada y rollback

Base nueva, upgrade, pérdida de red y manipulación de evidencia se prueban en proyecto/volumen **de prueba** con nombre distinto y puertos sin conflicto. Compartir únicamente GGUF read-only si es necesario; no duplicar una instancia GPU que exceda VRAM. Programar smoke serial del LLM existente o provisionar recurso aislado, sin terminar servicios de otros usuarios.

No usar `down -v` sobre el proyecto vigente, no rotar secretos ni tocar la cadena real. Respaldar/verificar DB de prueba antes de una migración nueva. Si se necesita una ampliación mínima para controles de contenido/correlación, documentar upgrade/compatibilidad/restore; no prometer downgrade destructivo. Rollback de imagen no hace compatibles datos si se cambió contrato: bloquear admisión hasta restaurar compatibilidad verificada.

Cierre AV-02: pruebas T-A01…T-A08 y smoke de imagen. Cierre AV-06: repetir sobre la configuración final sin redacciones manuales que oculten fallos.

## 6. Estado verificado en AV-00 (2026-11-10)

Estas comprobaciones se hicieron sobre el árbol `f81bd7d` y condicionaron AV-02. No son propuestas: son brechas medidas. La columna de acción indica el estado tras AV-02.

| Comprobación | Resultado real | Acción exigida en AV-02 |
|---|---|---|
| `migrator` en `compose.yaml` | Ejecuta solo `python -m alembic upgrade head`; **no** invoca `scripts/seed_policies.py`. DB nueva → 0 filas en `governance_policies`; smoke LLM real → 30 `BLOCK`/`NO_POLICY_MATCHED`. Tras seed manual (8 políticas) → `PASS`/`HITL` reales. | **Cerrada en AV-02**: `python -m argentgob.db.init` = upgrade + roles + seed + verificación, exit ≠ 0 propagado. T-A01 verifica `governance_policies > 0`. |
| Identidad HITL en el despliegue | `identity_fixture_roles={}` y `authorized_auditor_roles=[]` en `.env` y en el contenedor del dashboard; `ResolutionService` devuelve `outcome=blocked, reason=unauthorized`. | **Cerrada en AV-02**: ambas variables declaradas en `compose.yaml`; sonda en el contenedor devuelve `has_auditor=True`. T-B03/T-H02 prueban la configuración real del stack. |
| Rol de runtime | `argentgob_app` es `rolsuper=t`, `rolcreatedb=t`, `rolcreaterole=t`. Los roles de consola son `NOLOGIN` con grants mínimos. | **Cerrada en AV-02**: migraciones `008`–`010` crean `argentgob_runtime`/`argentgob_dashboard` sin superusuario ni DDL; T-A08 verifica mínimo privilegio. |
| Artefactos del predictor en la imagen | `RiskPredictor()` dentro de la imagen reporta `available=False` (`/app/data/models/risk_model_v1` no existe): el Containerfile no copia `data/`. | **Cerrada en AV-02**: `data/` se copia en las imágenes y `RISK_MODEL_DIR` apunta a `/app/data/models/risk_model_v1`; T-A07 verifica `available=True` sin bind mount. |
| Límites de payload | `max_json_depth`, `max_json_nodes`, `privacy_scan_timeout_ms` y `agent_profile` no se usan fuera de `config.py`. Solo `max_payload_bytes`, `max_string_bytes` y `rate_limit_calls_per_run` tienen efecto. | Pendiente: siguen sin aplicarse fuera de `config.py`; no documentarlos como activos. |
| E2E H8 sobre DB compartida | 22/23: `pending_approvals(limit=10)` ordena `argued_at ASC` y la DB acumula 25 PENDING. Sobre DB limpia: 23/23. | **Aplicado en AV-02**: la evidencia de gate corre sobre DB de prueba aislada, creada y eliminada en la sesión. |
