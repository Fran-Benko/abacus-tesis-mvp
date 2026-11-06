# Dataset Specification — Riesgo de tool call pre-ejecución

Fecha: 2026-11-06
Estado: **aprobada** (Fase 1 — Definición analítica)
Versión del esquema de features: `feature_schema_v1`
Documentos relacionados: [plan-continuacion.md](../plan-continuacion.md), [modulo2-entrega.md](../modulo2-entrega.md)

> Esta especificación define formalmente el problema de Ciencia de Datos de la
> capa predictiva (H12–H16) **antes** de construir el corpus (Fase 2).

---

## 1. Problema de Ciencia de Datos

| Aspecto | Definición |
|---|---|
| **Tipo** | Clasificación supervisada **multiclase** |
| **Unidad de observación** | Tentativa de tool call **antes** de su ejecución, representada por un `ToolCallEnvelope` (ver `src/argentgob/core/envelope.py`) |
| **Variable objetivo** | Nivel de riesgo de la tentativa: `LOW` / `MEDIUM` / `HIGH` |
| **Horizonte de predicción** | Tiempo de decisión (`pre_hook`), **antes** de la evaluación determinista y de cualquier side effect |
| **Dominio** | Middleware de gobernanza para agentes financieros (CrewAI) sobre herramientas `news`, `stock_price`, `crypto_price` |

### 1.1 Anti-circularidad de la etiqueta

La etiqueta objetivo **NO** es copia de:

- `observed_action` del motor determinista,
- la salida del motor ABAC/guardrails/policy engine (`PASS | BLOCK | HITL`),
- el resultado de la herramienta (que no existe aún en el `pre_hook`).

La etiqueta se construye a partir de **escenarios etiquetados** con una **rúbrica
humana** (guía de etiquetado, `docs/analytics/0002-labeling-guide.md`). Esto
garantiza que el modelo aprenda una señal de riesgo semántica y no memorice la
regla determinista (fuga de información).

> Consecuencia operativa: el corpus puede contener escenarios donde la política
> determinista dice `PASS` pero la rúbrica dice `HIGH` (y viceversa). Esa
> divergencia es **deseable** y se documenta como feature de control
> (`deterministic_action_hint` no se usa como target).

---

## 2. Variables predictoras (solo pre-ejecución)

Todas las features son derivables en el `pre_hook` a partir del
`ToolCallEnvelope` **sin ejecutar la herramienta**.

### 2.1 Contexto

| Feature | Tipo | Fuente en código | Descripción |
|---|---|---|---|
| `tool_name` | categórica | `envelope.tool_name` | `news`, `stock_price`, `crypto_price` |
| `operation_class` | categórica | `envelope.operation_class` | `READ`, `EXTERNAL_SEND`, `WRITE`, `DELETE`, `DDL`, `CODE_EXEC` |
| `agent_role` | categórica | `envelope.agent.role` | `analyst`, `analyst_restricted`, `admin` |
| `profile` | categórica | perfil resuelto | nombre del perfil activo |
| `environment` | categórica | `envelope.environment` | `LOCAL`, `TEST`, `PROD` |
| `resource` | categórica | `envelope.resource` | host/recurso normalizado |
| `hour_bucket` | ordinal (0–23) | `envelope.received_at` | hora UTC de la tentativa |

### 2.2 Payload y estructura

| Feature | Tipo | Derivación |
|---|---|---|
| `payload_size_bytes` | numérica | `envelope.payload_size_bytes` |
| `arg_count` | numérica | `len(execution_arguments)` sobre **telemetría sanitizada** |
| `json_depth` | numérica | profundidad máxima de la estructura JSON |
| `sanitized_text_length` | numérica | longitud del texto sanitizado |
| `has_url` | binaria | presencia de URL en la telemetría |
| `has_sql_injection_pattern` | binaria | match de patrones `_INJECTION_PATTERNS` de `module_c/guardrails.py` |
| `has_shell_pattern` | binaria | match de patrones shell |
| `pii_category_count` | numérica | cantidad de categorías PII detectadas (SECRET, EMAIL, PAYMENT_NUMBER, POSSIBLE_DNI) |
| `has_pii` | binaria | `pii_category_count > 0` |
| `dominant_data_type` | categórica | tipo de dato dominante entre los valores sanitizados |

> **Regla INV-04/05**: las features se calculan **siempre sobre
> `telemetry_arguments`** (versión sanitizada), nunca sobre `execution_arguments`
> crudos. El corpus NO persiste datos crudos.

### 2.3 Política y control (señales de gobierno, sin acoplar la etiqueta)

| Feature | Tipo | Derivación |
|---|---|---|
| `tool_allowed_for_profile` | binaria | herramienta permitida para el perfil (consulta ABAC) |
| `matched_policy_count` | numérica | cantidad de políticas coincidentes |
| `has_policy_conflict` | binaria | `policy_conflict` detectado |
| `max_allowed_sensitivity` | ordinal | sensibilidad máxima permitida por las políticas |
| `policy_is_active` | binaria | vigencia de la política (`valid_from`/`valid_until`) |
| `guardrail_results` | vector binario | resultado de detectores individuales (`query_injection`, `topic_relevance`, `rate_limit`) |
| `rate_limit_consumption` | numérica | consumo actual del rate limit |
| `operation_type_requested` | categórica | tipo de operación solicitada (syscall/método) |

> Estas features **describen el estado de control**, no la etiqueta. El riesgo
> se define por rúbrica; la señal de control puede correlacionar pero no la
> determina (ver §1.1).

---

## 3. Variables excluidas (fuga de información)

**Prohibidas en el esquema** (no se calculan ni se persisten en el corpus):

- `execution_arguments` crudos (INV-04/05).
- Embolsados / embeddings del texto crudo.
- `execution_status`, `latency_post_ms`, resultado de la herramienta.
- `observed_action` (acción final observada del motor).
- Cualquier campo del `post_hook` o de `execution_results`.

> Leakage por diseño: el modelo NO puede ver lo que aún no ocurrió. Si una
> feature requiere el resultado de la herramienta, no es elegible.

---

## 4. Tamaño y balance mínimo del corpus

| Parámetro | Valor |
|---|---|
| **Tamaño mínimo total** | 3 000 escenarios |
| **Distribución objetivo por clase** | `LOW` 50%, `MEDIUM` 30%, `HIGH` 20% |
| **Mínimo por clase** | `LOW` ≥ 1 200, `MEDIUM` ≥ 700, `HIGH` ≥ 500 |
| **Cobertura de herramientas** | las 3 (`news`, `stock_price`, `crypto_price`) |
| **Cobertura de perfiles** | los 3 (`analyst`, `analyst_restricted`, `admin`) |
| **Cobertura de clases de operación** | `READ`, `EXTERNAL_SEND`, `WRITE`, `DELETE`, `DDL`, `CODE_EXEC` |
| **Escenarios límite/conflicto** | ≥ 15% del corpus |

> Justificación: las clases son ordinales y hay interacciones no lineales
> (Random Forest); un mínimo de ~500 por clase con validación cruzada
> estratificada 5-fold da estabilidad a la estimación de `Recall de HIGH`.

---

## 5. Particiones (split)

| Partición | Proporción | Uso |
|---|---|---|
| **Entrenamiento** | 60% | ajuste de parámetros + validación cruzada estratificada |
| **Calibración** | 20% | calibración de probabilidades y selección de umbrales |
| **Evaluación final** | 20% | métricas finales reportables (congelada al cierre de Fase 2) |

Reglas:

- La partición de **evaluación final se congela al cierre de Fase 2** y no se
  vuelve a tocar hasta la evaluación final de Fase 3.
- El split es **estratificado por clase** (mantiene la distribución 50/30/20).
- El split se hace **por escenario** (no por agente): cada fila es una tentativa
  independiente. Se registra el `session_id` para análisis, pero NO se agrupa.
- Se usa `random_state` fijo y versionado para reproducibilidad.

---

## 6. Ciclo de vida y versionado

- El corpus se genera con un **script versionado** (Fase 2), con `seed` fija.
- Cada versión del corpus tiene un **identificador** (`corpus_v1`, `corpus_v2`, …)
  y un `corpus_manifest.json` (migración 005 relaciona `risk_model_versions` con
  `training_dataset_version`).
- El esquema de features queda versionado en `docs/analytics/risk_dataset_schema.json`
  (`feature_schema_version: "v1"`).

---

## 7. Criterio de salida (Fase 1)

- [x] Dataset specification versionada y aprobada (este documento).
- [x] Guía de etiquetado (`0002-labeling-guide.md`).
- [x] Riesgos de leakage identificados (§3) y mitigados por diseño.
- [x] Tamaño y balance mínimo definidos (§4).
- [x] Particiones definidas (60/20/20, §5).
