# Sesión 0014 — Fase 5: Interfaz y evidencia académica

> **Estado:** ✅ completada y liberada.
> **Rama:** `agents/fase5-evidencia` (worktree `fase5-evidencia`).
> **Base:** `main` @ `30fa798` (Fase 4).

---

## 1. Objetivo

Hacer visible la predicción y documentar los resultados: trazabilidad completa
entre corpus, modelo, predicción, decisión y visualización.

---

## 2. Qué se implementó

### 2.1 Servicios de evidencia

`src/argentgob/analytics/evidence.py`

- **`PredictionQueryService`**: consultas parametrizadas sobre
  `v_risk_evidence` (listado con filtros, métricas agregadas, divergencias,
  distribución temporal, versiones del modelo). Normaliza agregados `Decimal`
  de PostgreSQL a `float`.
- **`divergence_reason`**: clasifica la relación predicción ↔ decisión final en
  `CONSISTENTE`, `DETERMINISTA_MAS_ESTRICTO`, `DETERMINISTA_MENOS_ESTRICTO`,
  `SIN_DECISION`, `SIN_PREDICCION`.
- **`ModelEvidenceService`**: recalcula sobre la partición de evaluación
  congelada la matriz de confusión, métricas por clase, macro F1, balanced
  accuracy y curva de calibración. No usa valores hardcodeados.

### 2.2 Dashboard

`src/argentgob/dashboard/evidence.py` + integración en `app.py`

Sección «🔮 Fase 5 · Evidencia predictiva»:

- KPIs: predicciones, distribución LOW/MEDIUM/HIGH, HITL y BLOCK originados
  por predicción, tasa HITL predictiva, latencia media de inferencia.
- Tabla «Predicción vs decisión final» con filtros parametrizados y motivo de
  divergencia.
- Tabla agregada de motivos de divergencia.
- Distribución temporal de riesgos (gráfico de barras por día y clase).
- Versiones del modelo registradas y predicciones asociadas.
- Evaluación del modelo: matriz de confusión, métricas por clase y curva de
  calibración.

`compose.yaml`: el servicio `dashboard` monta `./data` en solo lectura para
acceder a los artefactos del modelo y al corpus.

### 2.3 Persistencia (migración 006)

`migrations/versions/006_risk_evidence_view.py`

- Vista `v_risk_evidence`: `risk_predictions LEFT JOIN governance_decisions`
  por `event_id` (17 columnas sanitizadas).
- Grants de solo lectura para `argentgob_console_viewer`, `_auditor`,
  `_operator` sobre la vista, `risk_predictions` y `risk_model_versions`.

### 2.4 Corrección de trazabilidad

`src/argentgob/analytics/store.py`: el segundo `record_prediction` (con
`decision_id`) era un no-op por `ON CONFLICT DO NOTHING`. Ahora usa
`ON CONFLICT (prediction_id) DO UPDATE SET decision_id = COALESCE(...)`, de
modo que la predicción queda efectivamente vinculada a la decisión final.

### 2.5 Tests y E2E

- `tests/unit/test_evidence.py` — **21 tests**: divergencias (9 casos
  parametrizados), consultas parametrizadas, INV-05 (sin payload crudo),
  métricas y tasa HITL, normalización Decimal→float, métricas reproducibles
  del modelo y metadatos versionados.
- `scripts/e2e_fase5_evidence.py` — **19/19 checks** contra PostgreSQL real:
  vista consultable, tool call gobernada con `AuditWriter` real, predicción
  vinculada a `decision_id`, decisión final presente, métricas, divergencias,
  distribución temporal, versiones, matriz 3×3 sobre 600 filas, macro F1,
  curva de calibración y ausencia de columnas de payload crudo.

### 2.6 Documentación

- `docs/analytics/0003-arquitectura-y-evidencia.md`: arquitectura, modelo de
  datos con cardinalidades, trazabilidad y separación de resultados.
- `docs/plan-continuacion.md`: Fase 5 marcada completada; baseline actualizado
  a 253 tests y migración 006.
- Este resumen.

---

## 3. Verificación

| Verificación | Resultado |
|---|---|
| Suite completa (`pytest tests/`) | ✅ **253 passed** (232 previos + 21 nuevos) |
| `tests/unit/test_evidence.py` | ✅ 21 passed |
| E2E Fase 5 (`scripts/e2e_fase5_evidence.py`) | ✅ **19/19** |
| Migración `006` | ✅ aplicada (`006 (head)`) |
| Imagen del agente reconstruida | ✅ `localhost/argentgob-mvp-agent:latest` |
| Imagen del dashboard reconstruida | ✅ `localhost/argentgob-mvp-dashboard:latest` |
| Dashboard (`:8599` en la validación) | ✅ HTTP 200, sección Fase 5 renderizada |
| Corpus | ✅ `RESULTADO: OK` |

---

## 4. Decisiones y notas

- **La evidencia se recalcula, no se copia.** `ModelEvidenceService` lee el
  pipeline y el corpus y vuelve a computar la matriz de confusión y la curva
  de calibración, de modo que la evidencia académica es reproducible y no
  depende de valores escritos a mano.
- **Divergencia explícita.** Se distingue «determinista más estricto» (el
  motor bloqueó algo que el modelo consideraba LOW/MEDIUM) de «determinista
  menos estricto» (el modelo pedía BLOCK/HITL y el motor dejó pasar). El
  segundo caso es el que interesa auditar.
- **`SIN_DECISION` vs `unlinked_decision`.** Las predicciones persistidas antes
  de la corrección del upsert no tienen `decision_id`; se cuentan por separado
  para no confundirlas con predicciones sin decisión final.
- **El dashboard tolera la ausencia de artefactos.** Si `data/` no está
  montado, la sección de evaluación del modelo muestra un aviso en lugar de
  fallar.

---

## 5. Próximos pasos

Con la Fase 5 cerrada, la hoja de ruta del plan queda completa. El trabajo
pendiente es de **prioridad alta** (no bloqueante para el prototipo):

1. Completar criterios de aceptación de H1–H4 y H8–H11.
2. Verificar HITL end-to-end con el modelo activo.
3. Detallar relaciones y cardinalidades en la entrega del Módulo 2.
4. Crear Sprint Backlogs con tareas, esfuerzo y dependencias.
