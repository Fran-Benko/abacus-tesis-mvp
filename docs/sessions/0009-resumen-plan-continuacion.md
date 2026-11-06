# Resumen de sesión — Plan de continuación y capa predictiva (respuesta a devolución E3)

Fecha: 2026-11-06
Rama: `main` (worktree raíz `plan-implementacion-avancemos`)
Plan: `mvp-plan-argentgob-v0.3-ampliado.md` (fuente normativa, NO está en el repo; está en `~/Downloads/`)

> Consideraciones generales del proyecto (comandos de verificación, topología
> Podman, trampas recurrentes y estado): **[PROJECT_CONSIDERATIONS.md](../PROJECT_CONSIDERATIONS.md)**.
> Hoja de ruta de continuación: **[plan-continuacion.md](../plan-continuacion.md)**.
> Instrucciones de sesión del agente: **[AGENTS.md](../../AGENTS.md)**.

## Objetivo de la sesión

Consolidar las definiciones y validaciones previas en un **plan de
continuación** y en **instrucciones de sesión persistentes**, y alinear ambos
con la **devolución del profesor del Entregable 3** (componente predictivo
ausente como capacidad real del producto). Esta sesión fue de **planificación y
documentación**; no se modificó código de producción.

## Qué se hizo en esta sesión

1. **Validación del análisis previo contra el repositorio real** (código,
   migraciones, políticas, resúmenes de sesión, git):
   - Confirmado: **203 passed** (187 unit + 16 integración contados por
     `def test_`), migraciones 001–004, H1–H8 completos.
   - Confirmado: **no existe componente predictivo** (grep por
     predict/risk_model/feature/inference en `src/` → 0 coincidencias).
   - Confirmado: la sanitización de PII es **100% determinista** (4 regex en
     `src/argentgob/module_b/sanitizer.py`); no hay "reconocimiento
     probabilístico".
   - Confirmado: `docs/modulo2-entrega.md` está **desactualizado** (describe
     H5/H6/H7, 161 tests, migraciones 001-003) y **untracked** en git.

2. **Creación de [`docs/plan-continuacion.md`](../plan-continuacion.md)** —
   hoja de ruta en 6 fases (0–5) para incorporar la capa predictiva H12–H16
   sobre la base H1–H11, con criterios de salida por fase.

3. **Creación de [`AGENTS.md`](../../AGENTS.md)** — instrucciones de sesión
   persistentes (auto-cargadas por el agente): dónde empezar, estado actual,
   stack Podman, verificación obligatoria al cierre (incluye **reconstruir y
   redeployar imágenes antes de las pruebas**), ciclo de vida de código/repo/
   branches, trampas recurrentes y convenciones.

## Cómo resolvemos las correcciones del profesor (E3)

La devolución del profesor tiene un **punto central**: el componente predictivo
debe ser una **capacidad real del producto**, no una línea futura. El plan de
continuación ataca cada observación:

| Observación del profesor | Cómo la resolvemos | Fase del plan |
|---|---|---|
| **Componente predictivo ausente** | Formular clasificación de riesgo de una tool call **antes** de su ejecución (LOW/MEDIUM/HIGH), con variable objetivo, predictoras pre-ejecución, modelos (logística + Random Forest), separación entrenamiento/calibración/evaluación e integración con PASS/BLOCK/HITL | Fases 1–4 |
| **Product Backlog sin funcionalidad predictiva** | Agregar H12 (corpus), H13 (features), H14 (entrenamiento/evaluación), H15 (inferencia), H16 (integración + dashboard) | Fase 5 (backlog) |
| **Historias de usuario sin criterios de aceptación (solo H5/H6/H7)** | Completar criterios de aceptación de H1–H4 y H8–H11, y redactar las nuevas H12–H16 con "qué usuario, qué resultado, qué decisión" | Fase 5 |
| **Sprint Backlog más plan por funcionalidad que descomposición operativa** | Descomponer sprints en tareas, esfuerzo y dependencias; ubicar corpus, preparación, entrenamiento, validación e integración en las iteraciones | Fase 5 |
| **Estructura de datos: falta relacionar corpus con esquema persistente** | Migración 005: `risk_model_versions` y `risk_predictions`; relacionar con `policy_decisions` y el corpus (atributos, relaciones, cardinalidades) | Fase 4 (migración 005) |
| **Sección 6.5: atributos, relaciones y cardinalidades** | Documentar explícitamente cardinalidades (1 modelo → N predicciones; 1 tool call → 0..1 predicción; 1 decisión → 0..1 predicción; 1 versión corpus → N modelos; 1 modelo → 1 esquema de features) | Fase 5 |
| **Dashboard no muestra el resultado predictivo** | Agregar riesgo estimado, clase, probabilidad/confianza, versión de modelo, decisión sugerida vs final, divergencia, distribución temporal, matriz de confusión, calibración, tasa de HITL por predicción | Fase 5 |
| **Arquitectura no permite seguir el flujo completo** | Actualizar arquitectura: Feature Builder → Risk Model → Model Registry/Artifact Store → Inference Service → motor de gobernanza | Fase 5 |
| **161 tests no sustituyen evaluación analítica** | Mantener separados 3 tipos de evidencia: tests de software, métricas analíticas de detectores, métricas predictivas del modelo | Fase 5 |

### Precedencia de decisión (integración predictiva)

1. Guardrail crítico o política BLOCK → **BLOCK**.
2. Predicción HIGH por encima del umbral → **BLOCK**.
3. Predicción MEDIUM o baja confianza → **HITL**.
4. Predicción LOW y políticas compatibles → **PASS**.
5. Error de inferencia → fallback determinista documentado.

Los controles deterministas (ABAC, guardrails, policy engine) se mantienen como
**barrera dura**; el modelo agrega una señal de riesgo para casos que las reglas
no resuelven por sí solas.

## Estado del repositorio

- Rama de trabajo: `main` (worktree raíz).
- Archivos nuevos (untracked):
  - `docs/plan-continuacion.md` — hoja de ruta de continuación.
  - `AGENTS.md` — instrucciones de sesión persistentes.
  - `docs/modulo2-entrega.md` — entrega Módulo 2 (ya existía, sigue untracked).
- No se modificó código de producción ni se agregaron tests.

## Próximo hito

**Fase 0 — Reconciliación técnica y documental** del
[`plan-continuacion.md`](../plan-continuacion.md):

1. Actualizar `docs/modulo2-entrega.md` a 203 tests / migración 004 / H8, y
   corregir PH (81/94), duplicación de H7 y conteo de políticas (6).
2. Commitear `modulo2-entrega.md`, `plan-continuacion.md` y `AGENTS.md`.
3. Verificar reproducibilidad (203 passed, E2E H8 23/23, cadena OK, migración
   004 head).

Luego seguir con **Fase 1 (definición analítica)** → **Fase 2 (corpus)** →
**Fase 3 (modelado)** → **Fase 4 (integración)** → **Fase 5 (interfaz y
evidencia)**.

## Verificación de cierre de sesión

Esta sesión fue de planificación/documentación (sin cambios de código), por lo
que no aplica el flujo completo de rebuild + tests. La suite sigue en **203
passed** y las migraciones en **004 (head)**. El flujo de verificación completo
aplica a partir de la Fase 0 cuando se modifique código o documentación
versionada.