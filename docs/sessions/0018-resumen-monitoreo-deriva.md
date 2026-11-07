# Resumen 0018 — Monitoreo de deriva del modelo (PSI)

> **Hito:** monitoreo de deriva del modelo (prioridad media del plan de continuación).
> **Rama:** `agents/hito9-monitoreo-deriva`.
> **Fecha:** 2026-11-07.

---

## 1. Contexto

El plan de continuación (`docs/plan-continuacion.md`) tenía cuatro ítems de
prioridad media. De ellos, el único con referencia explícita de
**"no implementado"** en la evidencia académica era el **monitoreo de deriva
del modelo** (`docs/analytics/0003-arquitectura-y-evidencia.md` §5):

> **Monitoreo de deriva:** no implementado; la tabla `risk_model_versions`
> deja el lugar para registrar nuevas versiones y comparar métricas.

Este hito lo implementa con **PSI (Population Stability Index)** entre la
distribución de referencia (corpus de entrenamiento) y la distribución
observada en producción.

---

## 2. Entregables

### 2.1 Núcleo de deriva (`src/argentgob/analytics/drift.py`)

- `psi(candidate, reference)`: PSI estándar con corrección de bins vacíos
  (epsilon) y guardas de masa cero (sin observaciones → 0, sin deriva medible).
- `psi_from_counts`: normaliza conteos crudos y computa el PSI.
- `severity(psi)`: clasifica la deriva en `sin_deriva` (< 0.10), `moderada`
  (0.10–0.25) y `significativa` (≥ 0.25).
- `build_class_distribution`: normaliza conteos por clase (LOW/MEDIUM/HIGH).
- `load_corpus_baseline`: carga la distribución de clases y de features
  canónicas (`FEATURE_ORDER`) desde `corpus_v1.parquet`; degrada a baseline
  vacío sin excepción si el corpus no existe.
- `observed_class_distribution`: distribución observada desde las
  predicciones persistidas (vía `PredictionQueryService.metrics()` o el store
  en memoria de los tests).
- `DriftReport` / `DriftDetector`: generan el reporte de deriva (PSI de
  clases, PSI por feature, severidad, tamaños de muestra) con `report_id`
  estable (digest) e invariante **INV-05** (nunca payload crudo).

### 2.2 Persistencia (`src/argentgob/analytics/drift_store.py` + migración 007)

- `DriftStore.record_report`: inserta el reporte en `model_drift_reports`
  (fail-open, mismo patrón que `PredictionStore`).
- `DriftStore.latest_reports`: últimos reportes para el dashboard.
- Migración `007_model_drift_reports.py`: tabla con `report_id` (PK),
  `class_psi`, `feature_psi` (JSON), distribuciones baseline/observada,
  severidad, tamaños de muestra y `features_compared`. Grants mínimos:
  `argentgob_app` (owner) escribe; viewer/auditor/operator solo SELECT.

### 2.3 Dashboard (`src/argentgob/dashboard/evidence.py` + `app.py`)

- Nuevo `render_drift_section()`: PSI de clases en vivo (baseline del corpus
  vs produción), severidad, distribución de clases comparada y últimos
  reportes persistidos. Integrado tras la sección de evidencia predictiva.

### 2.4 Tests

- **Unit** (`tests/unit/test_drift.py`, 10 tests): matemática del PSI
  (identidad = 0, drift total, bins vacíos, masa cero), `severity`, distribución
  de clases, `load_corpus_baseline` (distribución 50/30/20 del corpus y
  degradación sin parquet).
- **Integración** (`tests/integration/test_drift_integration.py`, 5 tests):
  flujo real con `_mem_db.py` — detector persistente + lectura, detección de
  drift significativo, degradación sin predicciones, observado vía
  `PredictionQueryService.metrics()`.
- **PostgreSQL real** (`test_postgres_real.py`, ahora 7 tests): head en 007,
  tabla `model_drift_reports` presente, grants mínimos y INSERT operativo.

---

## 3. Verificación

- Suite completa en contenedor: **286 passed + 6 skipped**
  (271 baseline + 10 unit nuevos + 5 integración nuevos; PG real se
  salta sin `TEST_DATABASE_URL`).
- PostgreSQL real (`TEST_DATABASE_URL=.../argentgob_test`): **7 passed** tras
  aplicar `alembic upgrade head` (006 → 007) en la DB real y de test.
- Migraciones aplicadas en el stack real: `alembic_version` = `007`.
- Ruff limpio en todos los archivos nuevos/modificados.

---

## 4. Pendientes de prioridad media (restantes)

- Refinar políticas declarativas.
- Mejorar visualizaciones históricas.
- Detector PII probabilístico.
