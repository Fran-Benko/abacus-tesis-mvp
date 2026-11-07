# Sesión 0015 — Fase 6: Tests de integración de flujos agénticos

> **Estado:** ✅ completada y liberada.
> **Rama:** `agents/fase6-tests-agencios` (worktree `fase6-tests-agencios`).
> **Base:** `main` @ `baaf579` (Fase 5).

---

## 1. Objetivo

Reproducir en los tests de integración los flujos reales que ejecutan los
usuarios finales: **análisis agénticos con agentes de IA** que recorren varias
tool calls. Validar que la solución **valide, analice, prediga y/o bloquee** lo
que corresponde en cada paso y que el resultado quede **reflejado en los
servicios de consulta del dashboard** (`ConsoleQueryService`,
`PredictionQueryService`) y en la **auditoría persistente**.

---

## 2. Qué se implementó

### 2.1 Tests de integración de flujos agénticos

`tests/integration/test_agentic_flows.py` (6 tests)

Reproduce un agente `analyst` recorriendo un análisis de mercado con varias
tool calls:

1. **Consulta benigna de precio** → PASS (predicción LOW).
2. **Búsqueda benigna de noticias** → PASS (predicción LOW).
3. **Inyección SQL** → BLOCK (predicción HIGH).
4. **PII en la consulta** → BLOCK (predicción HIGH).

A diferencia de los tests unitarios, se usan los **componentes reales**:

- `RiskPredictor` real (modelo versionado `risk_model_v1`) y `FeatureBuilder`
  real para la inferencia predictiva pre-ejecución.
- `ABACEvaluator` y `GuardrailEngine` reales (políticas del perfil).
- `AuditWriter` y `PredictionStore` reales, con la conexión a PostgreSQL
  reemplazada por una conexión en memoria que implementa las tablas/vistas de
  gobernanza (`governance_decisions`, `governance_events`,
  `risk_predictions`, `v_governance_console`, `v_risk_evidence`).

### 2.2 Tests

| Test | Verifica |
|---|---|
| `test_agente_analisis_mercado_valida_predice_bloquea` | Flujo completo: PASS benigno + BLOCK por riesgo; auditoría persiste 4 decisiones y 2 eventos |
| `test_decisiones_reflejadas_en_consola` | El dashboard (`ConsoleQueryService`) refleja PASS/BLOCK del flujo |
| `test_predicciones_reflejadas_en_evidencia` | El dashboard (`PredictionQueryService`) refleja las predicciones y los bloqueos por predicción |
| `test_auditoria_registra_decisiones_sin_payload_crudo` | INV-05: la auditoría persiste digests, nunca el payload crudo |
| `test_herramienta_no_permitida_bloquea_por_politica` | Un perfil restringido bloquea `news` por política (`TOOL_NOT_ALLOWED`) |
| `test_prediccion_medium_eleva_a_hitl_reflejado` | Una predicción MEDIUM eleva un PASS a HITL y se refleja en el dashboard |

---

## 3. Verificación

- ✅ Suite completa: **259 passed** (253 previos + 6 nuevos).
- ✅ Tests de integración: **22 passed** (16 previos + 6 nuevos).
- ✅ Lint limpio en `tests/integration/test_agentic_flows.py` (ruff F/E/W).
- ✅ El modelo real predice correctamente: inyección y PII → HIGH (BLOCK),
      escenarios benignos → LOW.

---

## 4. Documentación

- `docs/plan-continuacion.md`: sección 6.5 (Fase 6) y prioridad crítica
  actualizada.
- `AGENTS.md`: baseline actualizado a 259 tests y Fases 1–6.

---

## 5. Siguiente hito

La hoja de ruta (Fases 0–6) está **completa**. El trabajo pendiente es de
prioridad alta y no bloqueante: completar criterios de aceptación de H1–H4 y
H8–H11, verificar HITL end-to-end con el modelo activo, detallar relaciones y
cardinalidades en la entrega del Módulo 2, y descomponer los sprints en tareas
operativas.