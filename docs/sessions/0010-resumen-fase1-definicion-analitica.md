# Resumen de sesión — Fase 1: Definición analítica (capa predictiva)

Fecha: 2026-11-06
Rama: `main` (worktree raíz `plan-implementacion-avancemos`)
Hito: **Fase 1 — Definición analítica** de [`docs/plan-continuacion.md`](../plan-continuacion.md)

## Objetivo de la sesión

Formalizar el problema de Ciencia de Datos de la capa predictiva (H12–H16)
**antes** de construir el corpus: variable objetivo, features predictoras
pre-ejecución, etiqueta, particiones, tamaños y riesgos de leakage. Esta sesión
fue de **definición y documentación**; no se modificó código de producción.

## Entregables creados

| Entregable | Archivo | Contenido |
|---|---|---|
| Dataset specification | [`docs/analytics/0001-dataset-specification.md`](../analytics/0001-dataset-specification.md) | Problema (clasificación multiclase), unidad de observación (`ToolCallEnvelope` pre-ejecución), features (contexto / payload / control), excluidos por leakage, tamaño y balance, split 60/20/20, versionado |
| Guía de etiquetado | [`docs/analytics/0002-labeling-guide.md`](../analytics/0002-labeling-guide.md) | Rúbrica LOW/MEDIUM/HIGH por ejes (datos, operación, recurso, contenido, control), proceso con doble etiquetado (κ≥0.7), anti-patrones |
| JSON Schema del corpus | [`docs/analytics/risk_dataset_schema.json`](../analytics/risk_dataset_schema.json) | Esquema versionado `feature_schema_v1` con features + target + metadatos (partición, fuente, confianza) |

## Decisiones de diseño

1. **Unidad de observación**: tentativa de tool call **antes** de su ejecución
   (`ToolCallEnvelope`). El `pre_hook` es el punto de inyección natural (Fase 4).
2. **Variable objetivo**: `LOW` / `MEDIUM` / `HIGH` por **rúbrica humana**
   anti-circular (**NO** copia `observed_action` ni salida del motor
   determinista). La divergencia determinista-vs-rúbrica es una señal deseable.
3. **Features solo pre-ejecución** sobre `telemetry_arguments` sanitizados
   (INV-04/05): contexto (`tool_name`, `operation_class`, `agent_role`,
   `environment`, `resource`, `hour_bucket`), payload (`payload_size_bytes`,
   `arg_count`, `json_depth`, `sanitized_text_length`, `has_url`, patrones
   SQL/shell, `pii_category_count`, tipo dominante) y control (políticas
   coincidentes, conflictos, sensibilidad, guardrails, rate limit).
4. **Excluidos por fuga de información**: `execution_arguments` crudos,
   embeddings, `execution_status`, `latency_post_ms` y resultado de la herramienta.
5. **Tamaño mínimo**: 3 000 escenarios (LOW 50% / MEDIUM 30% / HIGH 20%).
6. **Split**: 60/20/20 estratificado por clase; evaluación final **congelada**
   al cierre de Fase 2.

## Verificación

- Los 3 entregables se crearon y quedaron versionados.
- El JSON Schema es válido (se validará programáticamente en Fase 2 al generar
  el corpus).
- No se modificó código de producción; la suite sigue en **203 passed** (baseline
  verificado en Fase 0).

## Próximo hito

**Fase 2 — Construcción del corpus**: generar un corpus sintético, controlado y
versionado (escenarios benignos, malignos, límites y conflictos; cobertura de
herramientas, perfiles y clases de operación; validación de ausencia de PII
real; controles de unicidad, completitud y dominio; congelar partición de
evaluación final). A partir de esta fase se agrega la **validación del corpus**
al flujo de cierre de sesión (AGENTS.md §4.4).
