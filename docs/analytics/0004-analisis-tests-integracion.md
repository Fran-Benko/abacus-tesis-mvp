# Análisis de los tests de integración — ¿se evalúa correctamente el funcionamiento?

> **Sesión 0015 — Fase 6.** Documento de análisis de los tests que se ejecutan
> para validar que la solución ArgentGob-Mesh evalúa correctamente el
> funcionamiento de la herramienta cuando la usan usuarios que ejecutan
> **análisis agénticos con agentes de IA**, y que no se está trabajando con
> sesgos.

---

## 1. Contexto

La solución es un middleware de gobernanza perimetral para agentes CrewAI en
el dominio financiero. El usuario final ejecuta **análisis agénticos**: un
agente de IA recorre varias tool calls (consultar precio, buscar noticias,
escribir, etc.) para producir un análisis. La solución debe, en cada paso:

- **Validar** (identidad, payload, límites).
- **Analizar** (features, riesgo predictivo).
- **Predecir** (clase de riesgo LOW/MEDIUM/HIGH).
- **Bloquear** (política, guardrail, predicción HIGH) o **elevar a HITL**
  (predicción MEDIUM).
- **Reflejar** el resultado en el dashboard y en la auditoría persistente.

Los tests de integración deben **reproducir esas situaciones** para validar
que la solución hace lo que corresponde y que el resultado queda reflejado.

---

## 2. Inventario de tests de integración

| Archivo | Tests | Qué cubre | Componentes usados |
|---|---|---|---|
| `test_governance_flow.py` | 6 | Flujo PEP: PASS, BLOCK por política/guardrail/payload, perfiles | ABAC + Guardrails reales, **AuditWriter mockeado**, sin predictor |
| `test_h7_audit_chain.py` | 5 | Cadena de auditoría: append/verify, manipulación, CLI | AuditChainAppender/Verifier reales, conexión en memoria |
| `test_h8_console.py` | 5 | Consola: consulta sanitizada, detalle HITL, resolución autorizada | ConsoleQueryService/ResolutionService reales, conexión en memoria |
| `test_agentic_flows.py` | 6 | **Flujo agéntico completo**: PASS benigno, BLOCK por predicción HIGH, BLOCK por política, HITL por predicción MEDIUM, reflejo en dashboard | **Todo real**: RiskPredictor, FeatureBuilder, ABAC, Guardrails, AuditWriter, PredictionStore, persistencia en memoria |

**Total: 22 tests de integración.**

---

## 3. Análisis de sesgos

### 3.1 Sesgo de "componentes aislados" (tests que no reproducen el flujo real)

Los tests de `test_governance_flow.py` usan un **`AuditWriter` mockeado**
(`MagicMock`). Esto valida que el PEP *llama* a `record_decision`/`record_result`,
pero **no valida que la decisión realmente se persista** ni que el dashboard
pueda leerla. Un bug en la persistencia (p. ej. el no-op de `ON CONFLICT DO
NOTHING` que se corrigió en Fase 5) **no se detectaría** con estos tests.

**Sesgo:** los tests pasan aunque la persistencia esté rota. No reproducen el
flujo real de un usuario que consulta el dashboard después de ejecutar un
análisis.

### 3.2 Sesgo de "sin capa predictiva"

Los tests de `test_governance_flow.py` no inyectan el `RiskPredictor` ni el
`PredictionStore`. Por lo tanto **no validan** que la predicción HIGH bloquee,
que la MEDIUM eleve a HITL, ni que las predicciones queden reflejadas en la
evidencia. La capa predictiva (el corazón de la tesis) queda **sin cobertura
de integración** en esos tests.

**Sesgo:** la solución podría degradar a fallback determinista (sin predicción)
y los tests seguirían pasando, dando una falsa sensación de que la capa
predictiva funciona.

### 3.3 Sesgo de "escenarios aislados" (una tool call por test)

Los tests de `test_governance_flow.py` y `test_h8_console.py` ejercitan **una
tool call o una consulta aislada** por test. No reproducen la situación real de
un **análisis agéntico**: un agente que recorre varias tool calls en una sesión,
con decisiones que se acumulan y se reflejan en el dashboard.

**Sesgo:** no se valida el comportamiento agregado (p. ej. que tras un análisis
de 4 tool calls el dashboard muestre 2 PASS y 2 BLOCK).

### 3.4 Sesgo de "dashboard desacoplado"

Los tests de `test_h8_console.py` construyen el `store` **a mano** (datos
fijos), no a partir de un flujo real. Esto valida que el `ConsoleQueryService`
lee bien, pero **no valida la trazabilidad** decisión → dashboard: que una
decisión generada por el PEP realmente aparezca en la consola.

**Sesgo:** el dashboard podría mostrar datos que nunca fueron generados por el
flujo real, o el flujo real podría generar datos que el dashboard no muestra.

### 3.5 Sesgo de "sin HITL end-to-end"

Los tests de `test_h8_console.py` validan la **resolución** de una approval
pre-existente (datos fijos), pero **no validan** que una predicción MEDIUM
genere la approval, que el HITL bloquee la ejecución y que la resolución
aprobada se refleje. El flujo HITL completo (predicción → approval → resolución
→ reflejo) no está cubierto de extremo a extremo.

**Sesgo:** la integración entre la capa predictiva y el HITL podría estar rota
sin que los tests lo detecten.

---

## 4. Qué se corrigió en la Fase 6

El nuevo `tests/integration/test_agentic_flows.py` (6 tests) ataca los sesgos
3.1, 3.2, 3.3 y 3.4:

- **Componentes reales** (no mocks): `RiskPredictor`, `FeatureBuilder`,
  `ABACEvaluator`, `GuardrailEngine`, `AuditWriter`, `PredictionStore`.
- **Persistencia en memoria** que implementa las tablas/vistas de gobernanza:
  `governance_decisions`, `governance_events`, `risk_predictions`,
  `v_governance_console`, `v_risk_evidence`.
- **Flujo agéntico completo**: un agente `analyst` recorre 4 tool calls
  (benigna → PASS, benigna → PASS, inyección → BLOCK por predicción HIGH, PII →
  BLOCK por predicción HIGH).
- **Reflejo en dashboard**: `ConsoleQueryService` (PASS/BLOCK/HITL) y
  `PredictionQueryService` (predicciones, bloqueos por predicción) leen el
  `store` generado por el flujo real.
- **INV-05**: la auditoría y las predicciones persisten digests, nunca el
  payload crudo.

Esto valida la **trazabilidad completa**: interceptar → sanitizar → predecir →
decidir → persistir → consultar (dashboard) → auditar.

---

## 5. Sesgos que persisten (recomendaciones)

> **Actualización (hito de prioridad alta):** los sesgos 1, 2 y 3 fueron
> abordados en `docs/sessions/0016-resumen-hito-prioridad-alta.md`.

| # | Sesgo | Estado | Cobertura |
|---|---|---|---|
| 1 | **HITL end-to-end** (3.5): la resolución aprobada no se refleja en el flujo | ✅ Resuelto | `tests/integration/test_hitl_end_to_end.py`: predicción MEDIUM (modelo activo) → approval → resolución APPROVED por auditor → resume → ejecución gobernada → reflejo en dashboard |
| 2 | **PostgreSQL real**: los tests usan conexión en memoria, no la DB real | ✅ Resuelto | `tests/integration/test_postgres_real.py` (skip sin `TEST_DATABASE_URL`): migraciones en head, constraints UNIQUE/CHECK, grants mínimos de roles de consola, flujo real LOW→PASS y vistas sanitizadas |
| 3 | **Concurrencia**: no se validan carreras entre agentes | ✅ Resuelto | `tests/integration/test_concurrency.py`: dos agentes paralelos (aislamiento, sin colisiones de `event_id`, idempotencia de approvals, digests sin contaminación) |
| 4 | **Cobertura de perfiles**: solo se prueban `analyst` y `analyst_restricted` | ⏳ Pendiente | Considerar `admin` y perfiles con transformación de argumentos |
| 5 | **CrewAI real**: los tests usan `SpyTool`, no una tool CrewAI real | ⏳ Pendiente | Los E2E de compatibilidad deberían usar CrewAI real (según la norma) |

---

## 6. Conclusión

La Fase 6 **redujo significativamente el sesgo** de los tests de integración
(flujos agénticos con componentes reales y reflejo en dashboard), y el hito de
**prioridad alta** cerró los tres sesgos más importantes que persistían: el
**HITL end-to-end** (predicción MEDIUM → approval → resolución → ejecución
gobernada), el uso de **PostgreSQL real** (constraints, roles y vistas) y la
**concurrencia** entre agentes. La suite completa pasó a **269 tests**
(263 unitarias/integración + 6 de PostgreSQL real).

Quedan como evolución posterior: ampliar la **cobertura de perfiles** (admin,
transformación de argumentos) y validar con **CrewAI real** en los E2E de
compatibilidad.