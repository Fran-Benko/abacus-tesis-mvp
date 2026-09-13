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
| `analyst`            | duckduckgo_news, stock_price, crypto_price | query_injection, topic_relevance, rate_limit |
| `analyst_restricted` | stock_price, crypto_price                | rate_limit                                   |
| `admin`              | duckduckgo_news, stock_price, crypto_price | (ninguno — solo debug)                       |

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
