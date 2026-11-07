# Resumen de sesión — Fase 3: Modelado

Fecha: 2026-11-06
Rama: `agents/fase3-modelado`
Hito: **Fase 3 — Modelado** de [`docs/plan-continuacion.md`](../plan-continuacion.md)
Baseline: 221 passed (212 previos + 9 nuevos de analytics; sin cambios en
`src/argentgob/`)

## Objetivo de la sesión

Entrenar, calibrar y evaluar los modelos candidatos de la capa predictiva
(baseline mayoritario, regresión logística multinomial, Random Forest) sobre el
corpus sintético versionado de Fase 2 (`corpus_v1`, split congelado), con foco
en **Recall de HIGH** y métricas reproducibles.

## Entregables creados

| Entregable | Archivo | Contenido |
|---|---|---|
| Entrenador | [`scripts/train_risk_model.py`](../../scripts/train_risk_model.py) | Baseline + logística multinomial + RF; 3 variantes de features (full / ctx_payload / control); CV 5-fold estratificada en train; calibración isotónica y selección de umbral HIGH sobre calibration; métricas finales sobre evaluation congelada; latencia P50/P95/P99; análisis de errores |
| Artefactos del modelo | [`data/models/risk_model_v1/`](../../data/models/risk_model_v1/) | `pipeline.joblib`, `thresholds.json`, `model_meta.json`, `experiment_results.json` (digest SHA-256) |
| Tests | [`tests/unit/test_analytics.py`](../../tests/unit/test_analytics.py) | 9 tests: preprocesador, encoding del target, split 60/20/20 estratificado, CV solo-train, umbral, métricas, anti-circularidad estructural, reproducibilidad, artefactos |
| Dependencia | [`pyproject.toml`](../../pyproject.toml) | `scikit-learn>=1.5` (capa predictiva) |
| Plan actualizado | [`docs/plan-continuacion.md`](../plan-continuacion.md) | Fase 3 marcada completa + resultado del modelo elegido |

## Resultados (partición evaluation congelada, 600 filas)

| Modelo | CV Macro F1 (train) | Macro F1 (eval) | Recall HIGH | Precision HIGH | Log Loss | Brier HIGH |
|---|---|---|---|---|---|---|
| Baseline mayoritario (LOW) | 0.222 | — | — | — | — | — |
| Regresión logística | 0.994 | **0.9985** | **1.0** | 1.0 | 0.034 | 0.010 |
| Random Forest | 0.999 | 1.0 | 1.0 | 1.0 | — | — |

- **Matriz de confusión (logística, eval):** `[[299,1,0],[0,180,0],[0,0,120]]`
  → 1 falso positivo LOW→MEDIUM; **0 falsos negativos de HIGH**.
- **Balanced Accuracy:** 0.9989. **Latencia inferencia:** p50 **11.6 ms**,
  p95 12.1 ms, p99 12.1 ms (500 filas, contenedor).
- **Descomposición de señal (CV train):**
  - `ctx_payload` (contexto+payload): log 0.936 / RF 0.977 → la señal
    semántica del escenario domina.
  - `control` (señales de gobierno): log 0.695 / RF 0.691 → las features de
    control **no determinan** la etiqueta (confirma la spec §2.3: "pueden
    correlacionar pero no determinan").
  - `full`: log 0.994 / RF 0.999 → combinar bloques da el mejor resultado.
- **Importancia de variables (RF):** `payload_size_bytes`, `sanitized_text_length`,
  `has_shell_pattern`, `matched_policy_count`, `has_policy_conflict` (top 5).
- **Coeficientes logísticos (clase HIGH):** `sanitized_text_length`,
  `payload_size_bytes`, `has_shell_pattern`, `has_sql_injection_pattern` positivos
  y dominantes (coherente con la rúbrica).
- **Análisis de errores:** RF y logística **discrepan en solo 1 fila** de
  evaluation; 0 FN de HIGH; perfil de FN vacío (no hay FN que perfilar).

## Decisiones de diseño

1. **Elección del modelo:** logística multinomial. RF (1.0) y logreg (0.9985)
   difieren ≤ 0.01 de Macro F1, por lo que se elige **regresión logística por
   interpretabilidad** (coeficientes auditables, requerimiento de la tesis).
2. **Descomposición de señal (variantes full/ctx_payload/control):** requisito
   de honestidad metodológica — el corpus sintético es **altamente separable**
   (operation_class `WRITE/DELETE/DDL/CODE_EXEC` siempre HIGH, señales de
   control correlacionadas con la rúbrica consistente). No es fuga de
   información (features pre-ejecución, sin `observed_action` ni resultado de
   herramienta), pero se documenta que la tarea es fácil para ambos modelos.
3. **Calibración isotónica** sobre `calibration` (20%), nunca sobre
   `evaluation`; la partición congelada se toca solo para métricas finales.
4. **Umbral HIGH** optimizado en calibration (max Recall con piso de Precision
   ≥ 0.5) → 0.30; en inference, si `P(HIGH) < 0.30` la clase decae a LOW.
5. **Reproducibilidad:** `--seed` fija CV, modelos y grid; digest SHA-256 de
   artefactos. Test que corre el entrenamiento 2 veces con seed 11 y compara.

## Bugs corregidos

- `LogisticRegression(multi_class=...)` removido en scikit-learn 1.4+ (error
  en 1.9.1); eliminado el kwarg (lbfgs ya es multinomial).
- `log_loss` con labels string desordenadas daba 33.9 (warning de sklearn);
  corregido calculándolo con el target codificado (ints) y labels en orden
  `TARGET_ORDER` → 0.034.
- Orden de columnas de `predict_proba` no era LOW/MEDIUM/HIGH con target
  codificado; `proba_in_order` lo normaliza.
- Importancia de variables del RF extraída del estimador interno (la
  calibración envolvente no la expone).

## Verificación

- `python scripts/train_risk_model.py --seed 42` → exit 0, modelo elegido:
  `logistic`.
- `pytest tests/unit/test_analytics.py` → **9 passed** (54s).
- Suite completa en contenedor → **221 passed** (212 baseline + 9 analytics).
- `python scripts/validate_corpus.py` → `RESULTADO: OK` (corpus intacto).
- Baseline de producción intacto (no se tocó `src/argentgob/`).

## Próximo hito

**Fase 4 — Integración operacional**: `RiskPredictor` (carga de artefacto +
esquema), `FeatureBuilder` (features pre-ejecución desde el `ToolCallEnvelope`),
inferencia en el `pre_hook`, persistencia (migración 005:
`risk_model_versions`, `risk_predictions`), precedencias PASS/BLOCK/HITL y
fallback seguro fail-closed. El pipeline `risk_model_v1` (logística calibrada,
umbral HIGH 0.30) queda versionado para integrar.