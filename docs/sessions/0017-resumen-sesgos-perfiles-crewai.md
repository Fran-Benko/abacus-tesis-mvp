# Resumen 0017 — Cierre de sesgos 4 y 5: perfiles adicionales y CrewAI real

> **Hito:** cierre de los dos sesgos pendientes de `docs/analytics/0004-analisis-tests-integracion.md`.
> **Rama:** `agents/hito8-perfiles-crewai` (merge `--ff-only` a `main`).
> **Fecha:** 2026-11-07.

---

## 1. Contexto

El análisis de la Fase 6 (`0004`) identificó 5 sesgos en los tests de
integración. El hito de prioridad alta (`0016`) cerró los sesgos 1–3 (HITL
end-to-end, PostgreSQL real y concurrencia). Quedaban como evolución posterior:

| # | Sesgo | Estado previo |
|---|---|---|
| 4 | **Cobertura de perfiles**: solo se prueban `analyst` y `analyst_restricted` | Pendiente |
| 5 | **CrewAI real**: los tests usan `SpyTool`, no una tool CrewAI real | Pendiente |

Este hito los cierra con 8 tests de integración nuevos.

---

## 2. Entregables

### 2.1 Sesgo 4 — Cobertura de perfiles (`tests/integration/test_perfiles_adicionales.py`)

6 tests con componentes reales (middleware, `RiskPredictor`, `AuditWriter`,
`PredictionStore`, persistencia en memoria `_mem_db.py`):

- **Perfil `admin`** (sin guardrails activos, `max_tool_calls=20`):
  - Una query no financiera (`"que hora es"`) que en `analyst` sería bloqueada
    por el guardrail `topic_relevance` (`TOPIC_NOT_FINANCIAL`) **se ejecuta en
    `admin`** porque el perfil no activa ese guardrail (predicción LOW → PASS).
  - Predicción LOW en `admin` no eleva la decisión a HITL.
- **Transformación de argumentos** vía `DeterministicEvaluator` end-to-end
  (la política decide, el middleware selecciona, la herramienta recibe):
  - `USE_TRANSFORMED_ARGUMENTS` → la herramienta recibe los argumentos
    efectivos normalizados (strip), nunca la preview; el digest persistido
    corresponde a los argumentos originales del envelope.
  - `USE_ORIGINAL_ARGUMENTS` → la herramienta recibe los argumentos intactos.
  - Ambas obligaciones juntas → `POLICY_CONFLICT` con cero ejecución (INV-02).

### 2.2 Sesgo 5 — CrewAI real (`tests/integration/test_crewai_real.py`)

3 tests que ejecutan la **`NewsTool` real** (no `SpyTool`) por la **ruta
pública `BaseTool.run()`** (la que CrewAI usa en producción), con providers
fakes inyectados (sin red):

- PASS con predicción LOW → el provider primario se consulta exactamente 1 vez,
  la herramienta devuelve el formato de noticias y la decisión/evento quedan
  persistidos.
- BLOCK por predicción HIGH → `HookAborted` sin consultar el provider (INV-02:
  cero requests no autorizados); la decisión BLOCK queda persistida y no hay
  evento de ejecución.
- BLOCK por guardrail `topic_relevance` → tampoco consulta el provider.

---

## 3. Verificación

- Suite completa (contenedor, código montado): **271 passed + 6 skipped**
  (263 baseline + 8 nuevos; los 6 skipped son los de `test_postgres_real.py`
  sin `TEST_DATABASE_URL`).
- Integración: **34 passed + 6 skipped** (26 baseline + 8 nuevos).
- Ruff limpio en los 2 archivos nuevos.
- Árbol git limpio.

## 4. Cómo reproducir

```bash
# Suite completa dentro del contenedor con el código local montado
docker run --rm -v "${PWD}:/app" -w /app \
  -e DATABASE_URL="postgresql+psycopg://argentgob_app:<pwd>@<ip>:5432/argentgob" \
  -e ENVIRONMENT=TEST -e LOG_LEVEL=WARNING \
  argentgob-mvp-agent:latest python -m pytest tests/ -q -p no:cacheprovider

# Solo los tests nuevos
docker run --rm -v "${PWD}:/app" -w /app \
  -e DATABASE_URL="postgresql+psycopg://argentgob_app:<pwd>@<ip>:5432/argentgob" \
  -e ENVIRONMENT=TEST -e LOG_LEVEL=WARNING \
  argentgob-mvp-agent:latest python -m pytest \
    tests/integration/test_perfiles_adicionales.py tests/integration/test_crewai_real.py \
    -v -p no:cacheprovider
```