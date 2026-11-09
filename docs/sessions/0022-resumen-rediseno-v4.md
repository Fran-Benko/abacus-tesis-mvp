# Resumen de sesión 0022 — Rediseño UX/UI y Executive Storytelling (prompt v4)

**Hito:** Rediseño integral del dashboard según `prompt_optimizado_v4.md`.
**Rama:** `agents/rediseno-dashboard-v4`.
**Fecha:** 2026-11-09

---

## Problema resuelto

El dashboard tenía defectos de presentación y datos (A1–A12, B1–B8): falsa
flecha de tendencia, 15 decisiones sin clasificar, aprobaciones HITL "zombis"
vencidas, logs con ANSI crudo, tablas crudas en vez de gráficos, refresco global
bloqueante, coloreado binario roto, agregación truncada a 200 filas, etc.

Este hito rediseña la capa de presentación a una **plataforma empresarial de
gobernanza de IA** con 5 vistas multipágina, sin tocar la lógica de negocio, las
autorizaciones ni INV-05.

---

## FASE 0 — Auditoría (con subagentes)

Se usaron **subagentes** para validar comportamientos erróneos y analizar datos:

1. **Auditoría de código** (general-purpose): confirmó/refutó B1–B8 y A1–A12
   con líneas exactas. Resultado: 10 confirmados, 1 refutado (A10: el código no
   produce ejes negativos), 3 parciales.
2. **Perfil de datos** (general-purpose): PostgreSQL consistente — 313 decisiones
   (67 PASS + 231 BLOCK + 15 HITL, suma cierra), 85 predicciones, 14 approvals
   (7 PENDING vencidas), 0 duplicados, 0 nulos, 1 approval_id legacy `apr-…`.
3. **Verificación de API Streamlit** (general-purpose): confirmó que **todo** lo
   que asume el prompt v4 existe en Streamlit 1.67.0 (nativo, sin
   `streamlit-echarts`).

Entregable: `docs/analytics/0005-fase0-auditoria-rediseno-v4.md`.

**Decisión de higiene:** la base está consistente → **no se borra nada**; solo
higiene presentacional (vencidas, huérfanas, IDs legacy).

---

## FASE 1 — Design system

- **`.streamlit/config.toml`**: tema claro editorial `#FAFAF8` (default) +
  variante oscura documentada (switch de un bloque).
- **`theme.py`**: paleta de colores (doble paleta), `strip_ansi` (regex ANSI
  centralizada), base ECharts, `estado_color`/`estado_texto_color`.
- **`charts.py`**: constructores JSON puros para `st.echarts_chart` (barras
  horizontales, heatmaps, curva de calibración, deriva) con `min: 0` en ejes de
  conteo y `renderer="svg"`.
- **`queries.py`**: `fetch_summary()` corregida (A2: incluye HITL/Otras),
  agregaciones sobre el universo completo (B1), `GROUP BY reason_code` (B4),
  saneo de nulos (A5), búsqueda robusta a IDs (A8).
- **`components.py`**: KPIs con proporciones neutras (sin flechas falsas),
  badges semánticos, `edad_humana`, estado del sistema.

## FASE 2 — Executive Overview

3 bloques, máximo 4 visualizaciones:
- **Cabecera**: entorno, última actualización, toggle pausa, popover
  "Simular tráfico E2E" (solo LOCAL), frase-resumen por reglas.
- **Bloque 1 · Salud y actividad**: 5 KPIs con proporciones neutras + gráfico
  de actividad por jornada (fragment 5s).
- **Bloque 2 · Contención**: decisiones por herramienta (barras horizontales
  apiladas) + motivos de bloqueo (GROUP BY reason_code).
- **Bloque 3 · Acción requerida**: cadena de auditoría + aprobaciones vencidas
  separadas (fragment 5s).

## FASE 3 — Vistas de profundidad

- **Auditoría y HITL**: consola sanitizada, filtros persistentes, cola
  vigentes/vencidas, resolución en `st.dialog` (lógica intacta).
- **Inteligencia Predictiva**: Producción (85) y Evaluación congelada (600)
  separadas; heatmap Predicción×Decisión, motivos de divergencia, matriz de
  confusión y curva de calibración como ECharts; `risk_probability` rotulada
  "Confianza de clase" (A4).
- **Salud del Modelo**: PSI global, severidad, features comparables (cobertura,
  no PSI por feature — B8), barras baseline vs observada, estado vacío.
- **Operaciones y Demo**: botones demo, resultados por escenario, consola de
  logs con `strip_ansi` + `wrap_lines` + filtro por nivel + búsqueda (fragment
  5s).

## FASE 4 — Testing y conciliación

- **17 tests** del design system (theme/charts/components/queries).
- **2 tests AppTest** (dashboard carga sin excepciones + no expone payload
  crudo) — validados con DATABASE_URL real.
- Suite completa: **334 passed + 9 skipped** (317 base + 17 nuevos).
- E2E H8 (23/23), Fase 4 (6/6), Fase 5 (19/19).
- Verificación en browser de las 5 vistas + demo end-to-end desde la UI
  (persistió 317→321 decisiones).

---

## Correcciones durante el desarrollo

- `st.Page` recibe la **función** `render`, no el módulo (TypeError corregido).
- El estado/log de la demo se envolvió en `@st.fragment(run_every="5s")` para
  que se actualice sin el loop global (B3).
- `Timestamp.utcnow` → `Timestamp.now("UTC")` (deprecación).
- Se eliminó `app.py` (entrypoint viejo); `dashboard.py` es el nuevo.

---

## Cómo usarla

1. Stack arriba: `podman compose up -d --force-recreate dashboard`.
2. `http://localhost:8501` → Executive Overview (default).
3. Navegación lateral: Auditoría y HITL · Inteligencia Predictiva · Salud del
   Modelo · Operaciones y Demo.
4. En Operaciones y Demo: "Correr demo (persistir)" → ver logs y KPIs
   actualizarse (fragment 5s).