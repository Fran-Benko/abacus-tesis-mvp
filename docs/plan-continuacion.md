# Plan de continuación — ArgentGob-Mesh MVP

> **Proyecto de tesis.** Documentación en español.
> Este plan consolida las definiciones y validaciones realizadas en sesiones
> previas para continuar el desarrollo del prototipo. Es la **hoja de ruta**
> para incorporar la capa predictiva (H12–H16) sobre la base de ingeniería ya
> implementada (H1–H11).

---

## 0. Contexto y decisiones tomadas

### 0.1 Estado actual verificado (baseline)

- **Suite de pruebas:** **203 passed** (192 unit + 11 integración).
- **Migraciones aplicadas:** 001 → 002 → 003 → **004** (head).
- **Hitos completos:** H1–H8 (intercepción, sanitización, ABAC, guardrails,
  policy engine persistente, HITL, auditoría encadenada, consola sanitizada).
- **Dashboard:** Streamlit con KPIs, decisiones, eventos, distribución por
  herramienta y sección H8 (consola sanitizada + resolución autorizada).
- **E2E H8:** `scripts/e2e_h8_console.py` → 23/23 checks OK.
- **Rama de trabajo:** `main` (worktree `plan-implementacion-avancemos`).

### 0.2 Validaciones realizadas (sesiones previas)

1. **"Reconocimiento probabilístico" de PII — NO existe.** La sanitización en
   `src/argentgob/module_b/sanitizer.py` es 100% determinista (4 regex fijos:
   SECRET, EMAIL, PAYMENT_NUMBER, POSSIBLE_DNI). No hay dependencias de
   ML/NER. La frase de la tesis E3 no corresponde al código.
2. **`modulo2-entrega.md` fue reconciliado en Fase 0:** ahora describe el
   estado real (203 tests, migraciones 001-004, H1-H8 completo, 81 PH de
   backlog, 94 PH de sprints, 6 políticas en YAML) y está versionado en git.
3. **El diseño predictivo se integra limpiamente** con el repositorio actual:
   el `pre_hook` y el `ToolCallEnvelope` son el punto de inyección natural; la
   persistencia (`policy_decisions.risk_level`), el dashboard y el HITL ya
   existen y son reutilizables.

### 0.3 Decisiones de alcance (pragmáticas)

| Decisión | Opción elegida | Justificación |
|---|---|---|
| **Alcance predictivo** | Regresión logística + Random Forest (comparación mínima defendible) | El análisis previo lo recomendó; balance entre interpretabilidad y poder predictivo |
| **Detector PII probabilístico** | Evolución posterior (backlog) | Mantener el foco en la capa predictiva; los regex siguen siendo la barrera dura |
| **Corpus** | Sintético generado por script (controlado y versionado) | Coherente con la definición de la tesis (corpus sintético, controlado, versionado) |
| **Reconciliación** | Actualizar y commitear `modulo2-entrega.md` primero | Evitar montar la capa predictiva sobre una base documental desactualizada |
| **Formato del plan** | Documento Markdown en `docs/` | Este archivo |

---

## 1. Fase 0 — Reconciliación técnica y documental

**Objetivo:** dejar el repositorio y la documentación en un estado reproducible
y coherente antes de incorporar la capa predictiva.

### 1.1 Actualizar `modulo2-entrega.md`

Corregir las inconsistencias detectadas:

- [x] Suite de pruebas: **161 → 203** (192 unit + 11 integración).
- [x] Migraciones: **001-003 → 001-004** (agregar `004_h8_console_roles.py`).
- [x] Agregar **H8** (consola sanitizada y resolución autorizada) al backlog,
      historias y estado.
- [x] Corregir el total de PH del backlog: **82 → 81** (3+5+5+8+13+13+13+3+5+5+8).
- [x] Corregir la suma de sprints: **84 → 94** (21+31+26+16).
- [x] Resolver la duplicación de **H7** en Sprint 2 y Sprint 3.
- [x] Aclarar el conteo de políticas: el "8" era de la corrida documentada; los
      YAML actuales suman **6** (admin 1, analyst 3, analyst_restricted 2).
- [x] Corregir la mención a "reconocimiento probabilístico" de PII (no existe;
      es regex determinista).
- [x] Commitear el documento (hoy está untracked).

### 1.2 Verificación de reproducibilidad

- [x] Ejecutar la suite completa: **203 passed**.
- [x] Ejecutar E2E H8: **23/23 checks OK**.
- [x] Verificar cadena de auditoría: `python -m argentgob.audit.cli` → `OK`.
- [x] Confirmar migraciones en `004 (head)`.

**Criterio de salida:** el repositorio reconstruye el mismo estado que los
contenedores actuales y la documentación refleja ese estado.

---

## 2. Fase 1 — Definición analítica

**Objetivo:** definir formalmente el problema de Ciencia de Datos y sus
variables antes de construir el corpus.

### 2.1 Problema de Ciencia de Datos

- **Tipo:** clasificación supervisada multiclase.
- **Unidad de observación:** tentativa de tool call **antes** de su ejecución.
- **Variable objetivo:** `LOW` / `MEDIUM` / `HIGH`.
- **Anti-circularidad:** la etiqueta NO debe ser copia de `observed_action` ni
  de la salida del motor determinista. Se construye a partir de escenarios
  etiquetados y criterios de riesgo definidos previamente.

### 2.2 Variables predictoras (solo pre-ejecución)

**Contexto:**
- `tool_name`, `operation_class`, `agent_role`, `profile`, `environment`,
  `resource`, `hour_bucket`.

**Payload y estructura:**
- `payload_size_bytes`, cantidad de argumentos, profundidad de la estructura
  JSON, longitud del texto sanitizado, presencia de URL, presencia de patrones
  SQL/shell, presencia y cantidad de categorías PII, tipo de dato dominante.

**Política y control:**
- Herramienta permitida para el perfil, cantidad de políticas coincidentes,
  existencia de conflicto, sensibilidad máxima permitida, vigencia de la
  política, resultado de detectores individuales, consumo de rate limit, tipo
  de operación solicitada.

**Excluidos (fuga de información):** embeddings, texto crudo, `execution_status`,
`latency_post_ms`, resultado de la herramienta.

### 2.3 Entregables

- [x] Dataset specification versionada (esquema de features + variable objetivo)
      → [`docs/analytics/0001-dataset-specification.md`](analytics/0001-dataset-specification.md).
- [x] Guía de etiquetado
      → [`docs/analytics/0002-labeling-guide.md`](analytics/0002-labeling-guide.md).
- [x] Identificación de riesgos de leakage (ver §3 de la spec: se excluyen
      `execution_arguments` crudos, embeddings, `execution_status`,
      `latency_post_ms` y el resultado de la herramienta).
- [x] Tamaño y balance mínimo del corpus (3 000 escenarios; LOW 50%, MEDIUM 30%,
      HIGH 20%; ≥15% de casos límite/conflicto).
- [x] Definición de particiones (60/20/20 estratificado por clase; evaluación
      final congelada al cierre de Fase 2).

**Criterio de salida:** dataset specification versionada y aprobada.

---

## 3. Fase 2 — Construcción del corpus

**Objetivo:** generar un corpus sintético, controlado y versionado.

### 3.1 Contenido

- [x] Escenarios benignos, malignos, límites y conflictos.
- [x] Cobertura de herramientas, perfiles y clases de operación.
- [x] Validación de ausencia de PII real.
- [x] Controles de unicidad, completitud y dominio.
- [x] Congelar la partición de evaluación final.

### 3.2 Formato

- Tabla PostgreSQL o archivo Parquet versionado (o ambos).
- Campos: etiqueta objetivo, variables del modelo, versión del esquema,
  partición asignada, fuente del escenario, estado de validación, identificador
  de versión del corpus.

**Entregables (Fase 2):**

- [x] Generador versionado → [`scripts/build_synthetic_corpus.py`](../scripts/build_synthetic_corpus.py)
      (`--seed 42 --size 3000`; balance 50/30/20 y split 60/20/20 estratificado).
- [x] Corpus Parquet versionado → [`data/corpus/corpus_v1.parquet`](../data/corpus/corpus_v1.parquet)
      (3000 escenarios; todo el corpus es **sintético** con placeholders, sin
      PII real).
- [x] Manifiesto → [`data/corpus/corpus_manifest.json`](../data/corpus/corpus_manifest.json)
      (versión, seed, split, hash de la partición evaluation congelada).
- [x] Reporte de validación → [`data/corpus/validation_report.json`](../data/corpus/validation_report.json)
      (PII real, unicidad, completitud, dominio, balance).
- [x] Validador → [`scripts/validate_corpus.py`](../scripts/validate_corpus.py)
      (valida contra `risk_dataset_schema.json` + controles; exit 0/1).
- [x] Tests del corpus → [`tests/unit/test_corpus.py`](../tests/unit/test_corpus.py)
      (8 tests: esquema, controles, PII, balance, split, dominios, congelado,
      anti-circularidad).

**Criterio de salida:** corpus versionado, validado y reproducible.
✔ Verificado: `python scripts/validate_corpus.py` → `RESULTADO: OK`.

---

## 4. Fase 3 — Modelado

**Objetivo:** entrenar, calibrar y evaluar los modelos candidatos.

### 4.1 Modelos

- [x] Baseline mayoritario.
- [x] Regresión logística multinomial (interpretable, coeficientes auditables).
- [x] Random Forest (captura interacciones no lineales, importancia de variables).

### 4.2 Evaluación

- [x] Validación cruzada estratificada dentro del entrenamiento.
- [x] Calibración de probabilidades (isotónica sobre `calibration`).
- [x] Selección de umbrales (umbral HIGH optimizado sobre `calibration`).
- [x] Métricas: Macro F1, Recall por clase, matriz de confusión, Balanced
      Accuracy, Log Loss, Brier Score, latencia P50/P95/P99.
- [x] Criterio principal: **Recall de HIGH** (un falso negativo de alto riesgo
      es más grave que una revisión humana adicional).
- [x] Análisis de errores (desacuerdo entre modelos + perfil de FN HIGH).

### 4.3 Entregables (Fase 3)

- [x] Entrenador versionado → [`scripts/train_risk_model.py`](../scripts/train_risk_model.py)
      (baseline + logística + RF; variantes de features full/ctx_payload/control;
      CV 5-fold estratificada en train; calibración isotónica y umbral sobre
      calibration; métricas sobre la partición evaluation congelada).
- [x] Artefactos del modelo → [`data/models/risk_model_v1/`](../data/models/risk_model_v1/)
      (`pipeline.joblib`, `thresholds.json`, `model_meta.json`,
      `experiment_results.json`; digest SHA-256 de artefactos).
- [x] Tests → [`tests/unit/test_analytics.py`](../tests/unit/test_analytics.py)
      (9 tests: preprocesador, encoding, split, CV, umbral, métricas,
      anti-circularidad, reproducibilidad, artefactos).
- [x] Dependencia `scikit-learn>=1.5` agregada a [`pyproject.toml`](../pyproject.toml).

### 4.4 Resultado

- **Modelo elegido:** regresión logística multinomial (Macro F1 0.9985 vs
  RF 1.0000; diferencia ≤ 0.01 → se prefiere interpretabilidad; coeficientes
  auditables, requerimiento de la tesis).
- **Métricas (evaluation congelada, 600 filas):** Recall HIGH **1.0**,
  Recall LOW 0.997, Recall MEDIUM 1.0; Balanced Accuracy 0.9989; Log Loss
  0.034; Brier HIGH 0.010; latencia p50 **11.6 ms** / p95 12.1 / p99 12.1.
- **Descomposición de señal (CV train):** `ctx_payload` domina (log 0.936 /
  RF 0.977); `control` solo 0.695/0.691 → las señales de gobierno NO determinan
  la etiqueta (spec §2.3 confirmada empíricamente).
- **0 falsos negativos de HIGH** en evaluation; RF y logística discrepan en 1
  fila. Reproducible: misma seed ⇒ mismas métricas y `artifact_digest`.

**Criterio de salida:** modelo elegido con métricas reproducibles.
✔ Verificado: suite completa **221 passed** (212 baseline + 9 analytics);
corpus sigue `RESULTADO: OK`.

---

## 5. Fase 4 — Integración operacional

**Objetivo:** incorporar la inferencia predictiva al flujo de gobernanza.

### 5.1 Componentes

- [ ] `RiskPredictor` (carga de artefacto + esquema de features).
- [ ] `FeatureBuilder` (extrae features pre-ejecución del `ToolCallEnvelope`).
- [ ] Inferencia en el `pre_hook` (antes de la decisión determinista).
- [ ] Persistencia de predicción y versión.
- [ ] Integración de precedencias PASS/BLOCK/HITL.
- [ ] Fallback seguro (modelo no disponible → motor determinista fail-closed).

### 5.2 Precedencia de decisión

1. Guardrail crítico o política BLOCK → **BLOCK**.
2. Predicción HIGH por encima del umbral → **BLOCK**.
3. Predicción MEDIUM o baja confianza → **HITL**.
4. Predicción LOW y políticas compatibles → **PASS**.
5. Error de inferencia → fallback determinista documentado.

### 5.3 Persistencia (migración 005)

- `risk_model_versions`: model_id, model_version, algorithm,
  feature_schema_version, training_dataset_version, trained_at, metrics_json,
  calibration_method, decision_thresholds, artifact_digest, status.
- `risk_predictions`: prediction_id, event_id, decision_id, model_id,
  model_version, predicted_risk_class, risk_probability, class_probabilities,
  inference_latency_ms, feature_schema_version, predicted_at.

### 5.4 Tests

- [ ] Unitarios (FeatureBuilder, RiskPredictor, precedencias, fallback).
- [ ] Integración (inferencia en el pre_hook + persistencia).
- [ ] Latencia (P50/P95/P99).

**Criterio de salida:** predicción ejecutada antes de la tool call y utilizada
por gobernanza.

---

## 6. Fase 5 — Interfaz y evidencia académica

**Objetivo:** hacer visible la predicción y documentar los resultados.

### 6.1 Dashboard

- [ ] Riesgo estimado, clase predicha, probabilidad/confianza, versión del modelo.
- [ ] Decisión sugerida por el modelo vs decisión final de gobierno.
- [ ] Motivo de divergencia entre predicción y decisión.
- [ ] Distribución temporal de riesgos.
- [ ] Matriz de confusión (conjunto de evaluación).
- [ ] Curva de calibración.
- [ ] Tasa de HITL originada por predicción.

### 6.2 Documentación

- [ ] Actualizar arquitectura (Feature Builder, Risk Model, Model Registry,
      Inference Service).
- [ ] Actualizar modelo de datos (relaciones y cardinalidades).
- [ ] Completar historias de usuario y criterios de aceptación H1-H4 y H8-H11.
- [ ] Descomponer sprints en tareas operativas.
- [ ] Relacionar corpus analítico y modelo persistente.
- [ ] Documentar resultados separando: tests de software, métricas analíticas
      de detectores, métricas predictivas del modelo.

**Criterio de salida:** trazabilidad completa entre corpus, modelo, predicción,
decisión y visualización.

---

## 7. Prioridad recomendada

**Prioridad crítica:**
1. Reconciliar repositorio e imágenes (Fase 0).
2. Definir etiqueta y features (Fase 1).
3. Construir corpus (Fase 2).
4. Entrenar y evaluar modelo (Fase 3).
5. Integrar inferencia antes de la ejecución (Fase 4).
6. Persistir y visualizar las predicciones (Fase 5).

**Prioridad alta:**
- Completar criterios de aceptación de H1-H4 y H8-H11.
- Verificar HITL end-to-end.
- Detallar relaciones y cardinalidades.
- Crear Sprint Backlogs con tareas, esfuerzo y dependencias.

**Prioridad media:**
- Refinar políticas declarativas.
- Mejorar visualizaciones históricas.
- Detector PII probabilístico (evolución posterior).
- Monitoreo de deriva (evolución posterior).

---

## 8. Convenciones de trabajo

- **Branches:** prefijo `agents/`, p. ej. `agents/h12-corpus-predictivo`.
- **Release:** no pushear/mergear hasta que pasen la suite unitaria y el E2E.
- **Commits:** prefijo convencional (`feat:`, `fix:`, `docs:`, `test:`) +
  trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- **Verificación:** usar la imagen `localhost/argentgob-mvp-agent:latest` con
  `-p no:cacheprovider`; E2E con `--network argentgob-mvp_default`.
- **Stack:** levantar siempre desde el worktree `plan-implementacion-avancemos`
  (el modelo GGUF solo existe ahí).