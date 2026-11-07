# Resumen de sesión — Fase 4: Integración operacional

Fecha: 2026-11-07
Rama: `agents/fase4-integracion`
Hito: **Fase 4 — Integración operacional** de [`docs/plan-continuacion.md`](../plan-continuacion.md)
Baseline: 232 passed (221 previos + 11 nuevos de la capa predictiva)

## Objetivo de la sesión

Incorporar la inferencia predictiva del modelo entrenado en Fase 3
(`risk_model_v1`, regresión logística calibrada, umbral HIGH 0.30) al flujo de
gobernanza: construir features pre-ejecución del `ToolCallEnvelope`, predecir
antes de la decisión determinista, persistir la predicción y aplicar las
precedencias PASS/BLOCK/HITL con fallback seguro fail-closed.

## Entregables creados

| Entregable | Archivo | Contenido |
|---|---|---|
| RiskPredictor | [`src/argentgob/analytics/predictor.py`](../../src/argentgob/analytics/predictor.py) | Carga pipeline.joblib + thresholds.json + model_meta.json; `predict_proba` normaliza clases a LOW/MEDIUM/HIGH; `predict` aplica umbral HIGH (decae a LOW si P(HIGH)<umbral); degrada a `None` si el modelo no está disponible |
| FeatureBuilder | [`src/argentgob/analytics/features.py`](../../src/argentgob/analytics/features.py) | Vector canónico de 27 features (context/payload/control) desde el envelope sobre **telemetría sanitizada** (INV-04/05, sin leakage); detector de inyección/PII; features de control derivadas del policy_source con degradación neutra |
| PredictionStore | [`src/argentgob/analytics/store.py`](../../src/argentgob/analytics/store.py) | Persistencia en `risk_predictions` (fail-open de observabilidad, patrón AuditWriter) |
| Integración PEP | [`src/argentgob/module_a/governance.py`](../../src/argentgob/module_a/governance.py) | Inferencia en `pre_hook` **antes** de la evaluación determinista; precedencias; `_elevate_hitl` reutiliza protocolo H6 |
| Migración 005 | [`migrations/versions/005_risk_predictions.py`](../../migrations/versions/005_risk_predictions.py) | `risk_model_versions` + `risk_predictions` con grants mínimos |
| Cableado agente | [`src/argentgob/agent/crew.py`](../../src/argentgob/agent/crew.py) | FeatureBuilder(RiskPredictor, PredictionStore) inyectados al middleware |
| Tests | [`tests/unit/test_analytics_integration.py`](../../tests/unit/test_analytics_integration.py) | 11 tests de la capa predictiva operacional |
| E2E | [`scripts/e2e_fase4_predictive.py`](../../scripts/e2e_fase4_predictive.py) | E2E contra PostgreSQL real (red argentgob-mvp_default) |
| Plan actualizado | [`docs/plan-continuacion.md`](../plan-continuacion.md) | Fase 4 marcada completa + resultado |

## Precedencia de decisión implementada

1. **Guardrail crítico o política BLOCK → BLOCK** (gana siempre).
2. **Predicción HIGH → BLOCK** (`ReasonCode.PREDICTED_HIGH_RISK`).
3. **Predicción MEDIUM (sobre PASS) → HITL** (`PREDICTED_MEDIUM_RISK`,
   reutiliza el protocolo H6 durable: hold + approval + notifier).
4. **Predicción LOW → PASS** (mantiene la decisión determinista).
5. **Error de inferencia o modelo ausente → fallback determinista**
   fail-closed (cero bloqueos por fallo de la capa predictiva).

## Resultados de la verificación

- **Suite completa en contenedor:** **232 passed** (221 baseline + 11 nuevos).
- **E2E Fase 4** (`scripts/e2e_fase4_predictive.py`, red real): **6/6 passed**
  — LOW→PASS ejecuta, HIGH→BLOCK (`blocked:PREDICTED_HIGH_RISK`, call_count 0),
  `risk_predictions` persistida (2 filas), fallback sin predictor PASS.
- **Migración 005 aplicada** contra PostgreSQL real (`004 -> 005`):
  `risk_model_versions` y `risk_predictions` creadas con owner `argentgob_app`.
- **Latencia de inferencia** (E2E): ~17–36 ms por predicción (well under el
  presupuesto de ~11.6 ms p50 del entrenamiento + overhead de I/O).

## Decisiones de diseño

1. **FeatureBuilder opera sobre telemetría sanitizada** (nunca
   `execution_arguments` crudos, INV-04/05). Los detectores de inyección/PII
   replican los patrones de `guardrails.py` y `sanitizer.py` sobre la vista
   sanitizada; si el sanitizador redacta el string (PII), la señal de
   inyección puede perderse — caso límite documentado, no viola la spec (la
   señal de control nunca fue determinante, §2.3).
2. **Capa predictiva opcional por inyección**: el `GovernanceMiddleware` acepta
   `feature_builder`/`risk_predictor`/`prediction_store` como parámetros
   opcionales. Sin ellos, degrada a fallback determinista (comportamiento
   pre-Fase 4 intacto). Los tests existentes del PEP y gobernanza siguen
   pasando sin cambios.
3. **Persistencia fail-open** (patrón AuditWriter): si la DB no está
   disponible, la predicción se loguea y el enforcement no falla.
4. **Umbral HIGH aplicado en inferencia**: si el argmax es HIGH pero
   P(HIGH) < 0.30, decae a LOW (mismo criterio que el entrenador Fase 3).
5. **Migración 005 con grants mínimos**: `argentgob_app` escribe ambas tablas;
   viewer/auditor/operator solo leen `risk_predictions` (preparando Fase 5).

## Bugs corregidos

- Indentación rota en `errors.py` al agregar los ReasonCodes predictivos
  (IndentationError de colección).
- `predict_proba` con `classes_` como `np.int64`: `'0' is not in list`
  → normalización con `int(cls)`.
- `return envelope` del `pre_hook` quedó desplazado al extraer `_elevate_hitl`
  → `governed_tool._run` recibía `None` (AttributeError); restaurado el return
  al final del `pre_hook`.
- Tests con mocks sin obligación de selección → `POLICY_CONFLICT`; se agregó
  `USE_ORIGINAL_ARGUMENTS` (exigencia R2).

## Próximo hito

**Fase 5 — Interfaz y evidencia académica**: dashboard con riesgo estimado,
clase predicha, probabilidad/confianza y versión del modelo; decisión sugerida
vs decisión final; motivo de divergencia; distribución temporal; matriz de
confusión y curva de calibración (evaluation); tasa de HITL originada por
predicción. Actualizar arquitectura y modelo de datos con la capa predictiva.
