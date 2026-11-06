# Resumen de sesión — Fase 2: Construcción del corpus sintético

Fecha: 2026-11-06
Rama: `agents/fase2-corpus-sintetico`
Hito: **Fase 2 — Construcción del corpus** de [`docs/plan-continuacion.md`](../plan-continuacion.md)
Baseline: 203 passed (sin cambios de producción)

## Objetivo de la sesión

Generar un corpus sintético, controlado y versionado de riesgo de tool call
pre-ejecución, alineado con la especificación de Fase 1
([`0001`](../analytics/0001-dataset-specification.md)) y la guía de etiquetado
([`0002`](../analytics/0002-labeling-guide.md)).

## Entregables creados

| Entregable | Archivo | Contenido |
|---|---|---|
| Generador | [`scripts/build_synthetic_corpus.py`](../../scripts/build_synthetic_corpus.py) | Escenarios benignos/malignos/límite/conflicto; features pre-ejecución sobre telemetría sanitizada; label por rúbrica anti-circular; split 60/20/20 estratificado; manifiesto + reporte |
| Corpus Parquet | [`data/corpus/corpus_v1.parquet`](../../data/corpus/corpus_v1.parquet) | 3000 escenarios, `feature_schema_v1`, particiones train/calibration/evaluation |
| Manifiesto | [`data/corpus/corpus_manifest.json`](../../data/corpus/corpus_manifest.json) | Versión `corpus_v1`, seed 42, split 60/20/20, hash de evaluación congelada |
| Reporte de validación | [`data/corpus/validation_report.json`](../../data/corpus/validation_report.json) | PII real (0), unicidad, completitud (0 nulos), dominio, balance |
| Validador | [`scripts/validate_corpus.py`](../../scripts/validate_corpus.py) | Valida contra `risk_dataset_schema.json` + controles de calidad; exit 0/1 |
| Tests | [`tests/unit/test_corpus.py`](../../tests/unit/test_corpus.py) | 9 tests: esquema, controles, PII, balance, split, dominios, congelado, anti-circularidad, reproducibilidad |
| Plan actualizado | [`docs/plan-continuacion.md`](../plan-continuacion.md) | Entregables de Fase 2 marcados como completados |

## Resultados del corpus

- **3000 escenarios** (LOW 1500 / MEDIUM 900 / HIGH 600 = 50/30/20).
- **Split** 60/20/20 estratificado por clase: train 1800 / calibration 600 /
  evaluation 600 (semilla fija `42`).
- **Cobertura**: 3 herramientas, 3 perfiles, 6 clases de operación, 3 entornos,
  6 recursos, 4 fuentes de escenario.
- **Límite/conflicto**: 943 (31.4% ≥ 15% requerido).
- **PII real**: 0 (todo el PII es sintético/placeholder; los emails usan dominio
  `.test`; los PAN son tarjetas de prueba).
- **Unicidad**: 3000 `record_id` únicos; 2848 perfiles de features únicos
  (152 duplicados por variación de hora/args del escenario).
- **Completitud**: 0 nulos / 0 NaN.
- **Dominio**: todas las columnas categóricas dentro de los enum del schema.
- **Schema JSON**: 0 incumplimientos sobre 3000 filas (validador compilado,
  4.7s).
- **Partición de evaluación congelada**: hash SHA-256 fijo en el manifest y
  verificado por el validador.

## Decisiones de diseño

1. **Anti-circularidad**: la etiqueta se asigna mediante **rúbrica humana**
   embebida en el generador (eje + evidencia documentados como
   `label.rationale`), nunca copiando `observed_action` ni la salida del motor
   determinista. Los escenarios de tipo `conflicto` contienen divergencias
   regla-vs-rúbrica deseables.
2. **Features solo pre-ejecución** sobre telemetría sanitizada (INV-04/05):
   el generador construye `context/payload/control` del esquema y el corpus
   **no persiste** `execution_arguments` crudos.
3. **Persistencia en Parquet versionado** (no PostgreSQL) para el corpus:
   reproducible con `--seed`, liviano, apto para Fase 3 (modelado) y sin
   dependencia del stack Podman para regen.
4. **Unicidad por perfil de features (no por record_id)**: la expansión varía
   ticker/query/hora/entorno/recurso para evitar un corpus casi constante; el
   indicador `duplicate_feature_rows` se reporta y queda en el manifiesto.
5. **Congelado de evaluación**: el hash de la partición `evaluation` se calcula
   sobre `record_id + label` y se persiste en el manifiesto; `validate_corpus.py`
   lo verifica en cada corrida.

## Verificación

- `python scripts/build_synthetic_corpus.py --seed 42 --size 3000` → exit 0,
  controles OK.
- `python scripts/validate_corpus.py` → `RESULTADO: OK — corpus versionado,
  validado y reproducible`.
- `pytest tests/unit/test_corpus.py` → **8 passed**.
- Baseline de producción intacto (203 passed; no se tocó `src/argentgob/`).

## Próximo hito

**Fase 3 — Modelado**: baseline mayoritario, regresión logística multinomial y
Random Forest; validación cruzada estratificada, calibración y selección de
umbrales; métricas con foco en **Recall de HIGH**; análisis de errores. El
corpus `corpus_v1` queda disponible para entrenar.