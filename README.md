# ArgentGob-Mesh — MVP

Middleware de **gobernanza perimetral** para agentes CrewAI. Intercepta cada
*tool call* de un agente financiero local, la evalúa contra políticas ABAC y
guardrails de dominio, y persiste evidencia auditable — todo **antes** de que la
herramienta produzca un efecto (INV-01 / INV-02).

> Proyecto de tesis. Toda la documentación, logs y comentarios están en español.

## Arquitectura (resumen)

```
Agente CrewAI  ──►  GovernedTool._run
                        │
                        ▼
          ┌─────────────────────────────┐
          │  Módulo A — PEP              │  pre_hook / post_hook
          │  (GovernanceMiddleware)     │
          └──────────────┬──────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                 ▼
  Módulo B          Módulo C          db.audit
  Sanitizer     ABAC + Guardrails    AuditWriter
                                     (PostgreSQL)
```

- **Módulo A (`module_a`)** — Policy Enforcement Point. Construye el
  `ToolCallEnvelope`, coordina la evaluación y decide `PASS`/`BLOCK`.
- **Módulo B (`module_b`)** — Sanitizador de telemetría: enmascara datos
  sensibles y trunca payloads largos sin mutar los argumentos operativos.
- **Módulo C (`module_c`)** — Evaluador ABAC por perfil + guardrails de dominio
  financiero (inyección, relevancia temática, rate limit).
- **Observabilidad** — `structlog` + auditoría en PostgreSQL + dashboard Streamlit.

## Perfiles disponibles

| Perfil               | Herramientas permitidas                  | Guardrails                                   |
|----------------------|------------------------------------------|----------------------------------------------|
| `analyst`            | news, stock_price, crypto_price          | query_injection, topic_relevance, rate_limit |
| `analyst_restricted` | stock_price, crypto_price                | rate_limit                                   |
| `admin`              | news, stock_price, crypto_price          | (ninguno — solo debug)                       |

## Requisitos

- Python 3.12+
- Podman ≥ 4.6 y podman-compose (para el stack completo)
- GPU NVIDIA opcional para el LLM local (cae a CPU si no está disponible)

## Puesta en marcha con el stack completo

```bash
make setup      # copia .env, crea secretos y descarga el modelo GGUF (~4.7 GB)
make start      # levanta PostgreSQL + llama-server y aplica migraciones
make agent ASSET=AAPL PROFILE=analyst
make dashboard  # http://localhost:8501
```

## Demo del dashboard en GitHub Codespaces

Codespaces despliega una variante ligera para mostrar el dashboard sin GPU ni
descarga del modelo GGUF. Este modo levanta PostgreSQL, el inicializador de
migraciones y Streamlit; no inicia el agente ni `llama-server`. Por eso
**Finanzas en vivo** informa que la API de runs no está disponible, mientras
que la demo determinista y las vistas que dependen de PostgreSQL siguen
disponibles.

Para desplegarla:

1. Abrir el repositorio en GitHub y crear un Codespace con la configuración de
   [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json)
   (Python 3.12 y Docker-in-Docker).
2. Al crear el entorno, [`.devcontainer/setup-demo.sh`](.devcontainer/setup-demo.sh)
   prepara `.env` y genera las credenciales locales en `.secrets/`;
   [`.devcontainer/build-demo.sh`](.devcontainer/build-demo.sh) intenta
   precargar las imágenes. Los valores se generan en el entorno y no deben
   copiarse al README, al repositorio ni a capturas o logs.
3. En cada arranque, [`.devcontainer/start-demo.sh`](.devcontainer/start-demo.sh)
   espera a Docker, descarga las imágenes publicadas en GitHub Container
   Registry y, si no están disponibles, las construye localmente. Después
   inicia [`compose.demo.yaml`](compose.demo.yaml) y espera el health check de
   Streamlit. Compose ejecuta los servicios con el UID/GID del Codespace para
   que puedan leer los secretos montados sin ampliar sus permisos.
4. En **PORTS**, abrir el puerto **8501**; la configuración del devcontainer
   solicita su reenvío público. El dashboard también se puede comprobar en
   `/_stcore/health`.

El [workflow de publicación de imágenes](.github/workflows/publish-images.yml)
publica las imágenes del dashboard y del inicializador en GHCR al actualizar
`main` en rutas relevantes, o mediante ejecución manual. La base de datos de
esta demo pertenece al entorno Codespaces y no convierte el Codespace en un
servicio permanente. Para el alcance, los límites y la verificación del
despliegue, consultar
[la guía de despliegue público](docs/implementacion-agente-vivo/despliegue-publico.md).

## Desarrollo local y pruebas

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
pytest tests/unit/ -v          # pruebas unitarias (no requieren base de datos)
pytest tests/ -v               # incluye pruebas de integración (con mocks)
```

Las pruebas unitarias e integración **no requieren** PostgreSQL: el `AuditWriter`
y el acceso a la base se reemplazan por mocks, y el evaluador ABAC usa las
políticas in-memory del perfil como *fallback* seguro.

## Demo end-to-end (gobernanza de principio a fin)

Ejecuta 5 escenarios sobre los componentes reales del middleware (PEP, policy
engine, guardrails, HITL, orquestación gobernada) con logs estructurados:

1. **Consulta benigna** → PASS (la tool ejecuta y se audita).
2. **Predicción HIGH** → BLOCK (`PREDICTED_HIGH_RISK`, la tool no ejecuta).
3. **Inyección SQL** → BLOCK (`INJECTION_PATTERN_DETECTED`).
4. **Sensibilidad declarativa** → BLOCK (`SENSITIVITY_EXCEEDED`, política YAML
   con `sensitivity_limit`).
5. **HITL completo** → approval → resolución de auditor → resume (token 1-uso)
   → ejecución gobernada exactamente una vez.

```bash
# En memoria (reproducible, sin red ni LLM)
docker run --rm -v "${PWD}:/app" -w /app \
  -e DATABASE_URL="$DATABASE_URL" -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO \
  --network bridge --entrypoint python argentgob-mvp-agent:latest \
  scripts/demo_end_to_end.py

# Persistiendo en PostgreSQL
docker run --rm -v "${PWD}:/app" -w /app \
  -e DATABASE_URL="$DATABASE_URL" -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO \
  --network bridge --entrypoint python argentgob-mvp-agent:latest \
  scripts/demo_end_to_end.py --real
```

## Invariantes clave

- **INV-01** — la herramienta nunca se ejecuta antes de una decisión válida.
- **INV-02** — un veredicto `BLOCK` aborta la tentativa (sin efecto secundario).
- **INV-04 / INV-05** — el payload crudo nunca se reutiliza ni se persiste; solo
  se almacena telemetría sanitizada y el digest SHA-256.
- **INV-11** — ante duda, la política falla cerrada (`BLOCK`).

## Estructura del proyecto

```
src/argentgob/       Código fuente (core, module_a/b/c, db, tools, agent, dashboard)
migrations/          Migraciones Alembic (schema PostgreSQL)
policies/            Políticas ABAC declarativas (YAML)
prompts/             System prompts por perfil
scripts/             Utilidades (descarga de modelo, seed de políticas, verificación)
containers/          Containerfiles (agente y dashboard)
tests/               Pruebas unitarias y de integración
```

## Licencia

MIT.
