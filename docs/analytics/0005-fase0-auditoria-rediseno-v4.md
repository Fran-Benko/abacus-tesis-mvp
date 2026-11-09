# FASE 0 — Auditoría de código, perfil de datos y verificación de API

**Rama:** `agents/rediseno-dashboard-v4`
**Fecha:** 2026-11-09
**Fuente:** prompt_optimizado_v4.md (§3, §4, §16) — hallazgos A1–A12, B1–B8.

Este documento consolida la auditoría realizada con subagentes sobre el código
real, la base de datos y la API de Streamlit instalada. Es el insumo de la FASE 0
antes de tocar código.

---

## 1. Auditoría de código (hallazgos B1–B8 y A1–A12)

Archivo principal del dashboard: `src/argentgob/dashboard/app.py` (el prompt
menciona `dashboard.py`, pero el archivo real es `app.py`).

| Hallazgo | Veredicto | Ubicación | Detalle |
|---|---|---|---|
| B1 · truncado a 200 | ✅ CONFIRMADO | `app.py:53,68,311,339` | `fetch_decisions(limit=200)` + `groupby` sobre las 200 filas |
| B2 · coloreado binario | ✅ CONFIRMADO | `app.py:316-321` | `color_action` pinta HITL de rojo igual que BLOCK |
| B3 · refresco global | ✅ CONFIRMADO | `app.py:374-375` | `time.sleep(5)` + `st.rerun()` al final, fuera del try |
| B4 · reason_code | ⚠️ Parcial | `app.py:62,323` | Truncamiento visual (default Streamlit); **no existe GROUP BY reason_code** |
| B5 · eje temporal | ✅ CONFIRMADO | `evidence.py:149-154` | `tdf.pivot(index="day")` + `st.bar_chart` |
| B6 · latencia nula | ✅ CONFIRMADO | `evidence.py:79` | `f"{metrics['avg_latency_ms'] or 0:.2f} ms"` → `0.00 ms` |
| B7 · st.json crudo | ✅ CONFIRMADO | `app.py:199,229` | `st.json(det)` y `st.json(result)` |
| B8 · PSI por feature | ✅ CONFIRMADO | `evidence.py:281` | `len(feature_psi_support)` = cobertura, no PSI por feature |
| A1 · flecha falsa | ✅ CONFIRMADO | `app.py:303-304` | `pass_pct = f"{100 * total_pass // total}%"` como `delta` |
| A2 · 15 sin clasificar | ✅ CONFIRMADO | `app.py:74-84` | `fetch_summary()` solo cuenta PASS y BLOCK |
| A7 · ANSI en logs | ✅ CONFIRMADO | `app.py:287` | `st.code(texto)` directo sin limpiar ANSI |
| A10 · eje Y negativo | ❌ REFUTADO | `app.py:339-345` | Conteos `size()` siempre ≥ 0; `fillna(0)` |
| A11 · matrices crudas | ✅ CONFIRMADO | `evidence.py:209,213,226,296,304` | `st.dataframe` crudos |
| A12 · truncamiento | ⚠️ Parcial | `app.py:287,323` | Rotación automática de `st.bar_chart`; IDs truncados visualmente; logs a 500 líneas sin wrap |

**Nota A10:** el prompt afirma eje Y con −50, pero el código usa conteos (`size()`)
que no pueden ser negativos. El −50 observado en la captura probablemente proviene
del rango automático de Vega-Lite con datos apilados; aun así se fijará `min: 0`.

---

## 2. Perfil de datos (PostgreSQL `argentgob`)

### 2.1 Conteos por tabla
| Tabla | Filas |
|---|---|
| governance_decisions | **313** |
| governance_events | **67** |
| approval_requests | **14** |
| policy_decisions | **37** |
| execution_results | **6** |
| audit_chain | **1** |
| risk_predictions | **85** |
| model_drift_reports | **0** (vacía) |
| approval_resume_paleta | 13 |
| governance_policies | 8 |
| risk_model_versions | 1 |

### 2.2 governance_decisions
- TOTAL **313** · PASS **67** · BLOCK **231** · HITL **15** · otros/NULL **0**
- **La suma cierra exacto: 67+231+15 = 313** (confirma A2: los 15 son HITL)
- AVG(latency_pre_ms) **23.38 ms** · sin NULL ni 0 en latencia
- tool_name: news 111 · stock_price 108 · duckduckgo_news 82 · crypto_price 12 (sin NULL)
- reason_code top: RATE_LIMIT_EXCEEDED 93 · NO_POLICY_MATCHED 90 · ALLOWED 67 ·
  TOOL_NOT_ALLOWED 18 · PREDICTED_MEDIUM_RISK 15 · TOOL_PROVIDER_UNAVAILABLE 12 ·
  PREDICTED_HIGH_RISK 9 · INJECTION_PATTERN_DETECTED 9
- Fechas: min 2026-09-14 · max 2026-11-08 · **5 jornadas activas**
  (14/17/19 sep → 141/25/96; 7/8 nov → 6/45)

### 2.3 approval_requests
- PENDING **7** · APPROVED **7** · REJECTED/EXPIRED **0**
- **PENDING: 0 vigentes · 7 vencidas** (todas). El esquema usa `expires_at`, no
  `expires_in_seconds`.
- Vencidas: `apr-5250c5df8019` (47.6 días) + 6 UUIDs del 2026-11-08 (~1.6 días)

### 2.4 risk_predictions
- TOTAL **85** · LOW **53** · MEDIUM **15** · HIGH **17**
- Con decision_id **83** · Sin decision_id **2** (huérfanas)

### 2.5 Formatos de ID (A8)
- decision_id: **313/313 UUID** · event_id: **67/67 UUID**
- approval_id: **13 UUID + 1 prefijado** (`apr-5250c5df8019`) → **mezcla** (1 legacy)

### 2.6 Duplicados (A9)
- `governance_decisions` no tiene `argument_digest`/`idempotency_key` (solo
  `payload_digest`); **0 duplicados** por payload_digest.
- `risk_probability=0.4462` exacto: **0**; valores cercanos (0.446033 ×4, 0.446074
  ×5) son probabilidades reales del modelo, no duplicados.

### 2.7 Nulos (A5)
- tool_name NULL **0** · agent_role NULL **0** · reason_code NULL **0**

### 2.8 Conclusión de higiene (§4)
La base está **consistente**: sin nulos, sin duplicados, suma de categorías cierra.
**No se requiere borrado.** La única higiene es **presentacional**:
- 7 approvals PENDING vencidas → separar vigentes/vencidas en la UI (A3).
- 2 predicciones huérfanas sin decision_id → rotular «Sin decisión vinculada» (A5).
- 1 approval_id legacy `apr-…` → búsqueda robusta a ambos formatos (A8).
- `model_drift_reports` vacía → estado vacío profesional (no inventar PSI por feature).

---

## 3. Verificación de API Streamlit 1.67.0

Versión instalada: **1.67.0** (pyproject dice `>=1.35`, pero el entorno tiene 1.67.0).
`streamlit_echarts` **NO está instalado** y **no hace falta**: `st.echarts_chart`
es nativo desde 1.37.

| API | Estado | Firma relevante |
|---|---|---|
| `st.echarts_chart` | ✅ nativo | `(spec, *, width='stretch', height='content', theme='streamlit', key=None, on_select='ignore', renderer='canvas', alt=None)` |
| `st.badge` | ✅ | `(label, *, icon=None, color='blue', width='content', help=None)` — color: red/orange/yellow/blue/green/violet/gray/grey/primary |
| `st.fragment` | ✅ | `(func=None, *, run_every=None, parallel=False, key=None)` — soporta `run_every="5s"` |
| `st.navigation`/`st.Page` | ✅ | `Page(page, *, title=None, icon=None, url_path=None, default=False, ...)` — soporta `icon=":material/...:"` y `default=True` |
| `st.dialog` | ✅ | `(title, *, width='small', position='center', dismissible=True, icon=None, ...)` |
| `st.code` | ✅ | `(body, language='python', *, line_numbers=False, wrap_lines=False, ...)` — soporta `wrap_lines=True` |
| `st.dataframe` | ✅ | soporta `column_config` |
| `st.metric` | ✅ | `delta_arrow='auto'|'up'|'down'|'off'` — `delta_arrow="off"` válido |
| `st.popover` | ✅ | soporta `icon` |
| `st.testing.v1.AppTest` | ✅ | requiere `from streamlit.testing.v1 import AppTest` |
| `st.grid` | ✅ | soporta `wrap` y `min_column_width` |
| `st.column_config` | ✅ | TextColumn, NumberColumn, ProgressColumn, etc. |

**Correcciones al prompt v4:**
- `st.echarts_chart` **no requiere** `streamlit-echarts` (nativo).
- `selectedMode` va **dentro del spec** de ECharts, no como kwarg del widget.
- `AppTest` requiere import explícito (`from streamlit.testing.v1 import AppTest`).

---

## 4. Decisiones de FASE 0

1. **Higiene de datos**: no borrar nada (base consistente). Solo higiene
   presentacional (vencidas, huérfanas, IDs legacy).
2. **A4 (risk_probability)**: el perfil muestra que `risk_probability` es la
   confianza de la clase predicha (score calibrado isotonic), no P(HIGH)
   acumulada. Se rotulará «Confianza de clase» con tooltip. El umbral 0.3
   pertenece a la evaluación congelada (n=600), no al runtime.
3. **A10**: fijar `min: 0` en ejes de conteo (defensa, aunque el código no
   produce negativos).
4. **Arquitectura**: adoptar `st.navigation` + `st.Page` con 5 vistas, separando
   acceso a datos (`queries.py`) / presentación (`pages/`) / tema (`theme.py`) /
   gráficos (`charts.py`).