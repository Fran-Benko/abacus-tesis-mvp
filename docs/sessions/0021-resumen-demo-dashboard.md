# Resumen de sesión 0021 — Demo end-to-end en vivo desde el dashboard

**Hito:** Botones en el dashboard para correr la demo en tiempo real y ver cómo
los logs y los gráficos van cambiando (auto-refresh de 5 s).
**Rama:** `agents/dashboard-demo-en-vivo`.
**Fecha:** 2026-11-08

---

## Problema resuelto

El dashboard mostraba KPIs, decisiones, eventos y evidencia predictiva con
auto-refresh cada 5 s, pero **no había forma de disparar la demo end-to-end
desde la UI**: para ver el flujo de gobernanza llenándose había que correr
`scripts/demo_end_to_end.py --real` a mano en otro contenedor.

Este hito agrega una sección **"🚀 Demo end-to-end en vivo"** con:

- Botón **▶️ Correr demo (persistir)** → lanza la demo `--real` como subproceso
  del proceso Streamlit (persiste en PostgreSQL, sin LLM: predictor stub).
- Botón **🧹 Limpiar log** → vacía el log acumulado sin cancelar el proceso.
- Estado en vivo: `Demo en ejecución` → `Demo finalizada (exit N)`.
- **Resumen por escenario** (PASS / BLOCK ×3 / HITL) sin payload crudo (INV-05).
- **Log estructurado en streaming** (últimas 500 líneas) con los eventos de
  gobernanza (`policy_block`, `guardrail_pass`, `predictive_inference`,
  `approval_*`, `hitl_*`, `tool_call_complete`).

Como el dashboard ya hace `st.rerun()` cada 5 s, los **KPIs, tablas y gráficos**
de arriba se actualizan solos a medida que la demo persiste: es el "tiempo
real" pedido, sin infraestructura extra (WebSockets, colas, etc.).

---

## Diseño

### `src/argentgob/dashboard/demo_runner.py` (nuevo)
Proceso único por dashboard (módulo-level, con `threading.Lock`):

- `start_demo()` → `subprocess.Popen([python, scripts/demo_end_to_end.py,
  "--real"], cwd=/app)` con `PYTHONUNBUFFERED=1`; no-op si ya hay uno activo.
- `_worker()` → dos hilos (uno por pipe stdout/stderr) leen en streaming y
  acumulan en `_state["lines"]`; al terminar setean `done`, `returncode` y
  parsean los escenarios.
- `demo_status_snapshot()` → devuelve `running/started_at/finished_at/
  returncode/done/escenarios/lines` (últimas 500 líneas).
- `limpiar_log()` → vacía buffer y escenarios.
- `_parse_escenarios()` → extrae `titulo/outcome/explicacion` de las narrativas
  `ESCENARIO:` y del bloque `RESUMEN DE LA DEMO`, deduplicando por
  (título, outcome). No expone arguments: solo metadata (INV-05).
- `demo_available()` → True si `scripts/demo_end_to_end.py` existe en la imagen
  (evita error feo si alguien corre el dashboard sin scripts).

Notas de robustez:
- **Windows/host**: se evita `select.select` sobre pipes (no funciona); se usa
  un hilo lector por pipe.
- El proceso es hijo del Streamlit; el `st.rerun()` de 5 s no lo interrumpe.

### `src/argentgob/dashboard/app.py` (modificado)
- `_render_demo_section()`: botones, estado, resumen por escenario y `st.code`
  con el log en vivo; llamado al final del cuerpo principal (después de
  `render_drift_section()`).

### `containers/dashboard.Containerfile` (modificado)
- `COPY scripts/ ./scripts/` y `COPY policies/ ./policies/`: la demo usa
  `scripts/demo_lib.py`, `scripts/demo_end_to_end.py` y lee `policies/*.yaml`
  (fuente declarativa del escenario de sensibilidad), todos relativos a /app.

### `tests/unit/test_dashboard_demo_runner.py` (nuevo)
5 tests unitarios (sin subprocesos ni DB):
- `demo_available()` True cuando el script existe.
- `_parse_escenarios()` captura titulo/explicacion/outcome (narrativa completa +
  bloque RESUMEN, con dedupe correcto).
- El parser ignora logs estructurados structlog.
- Snapshot inicial con estado vacío.
- `limpiar_log()` vacía el buffer.

---

## Verificación

- `pytest tests/unit/test_dashboard_demo_runner.py` → **5 passed**.
- Suite completa en contenedor: **312 passed + 7 skipped + 5 nuevos** (total 317
  passed + 7 skipped).
- Imagen `localhost/argentgob-mvp-dashboard:latest` reconstruida y
  redeployada (`podman compose up -d --force-recreate dashboard`).
- **Demo `--real` desde la UI (click en el botón)**: `Demo finalizada (exit 0)`,
  resumen por escenario visible y log con `policy_block` / `guardrail_block` /
  HITL; las decisiones pasaron de 304 → 308 y los eventos de 64 → 65 en
  PostgreSQL (persistencia real).
- Verificación en browser (Streamlit en :8501) de la sección demo renderizada
  con ambos botones.

---

## Cómo usarla

1. Stack arriba (postgres + dashboard). La demo **NO requiere llama-server**
   (predictor stub interno).
2. Abrir `http://localhost:8501`.
3. Sección **"🚀 Demo end-to-end en vivo"** → **▶ Correr demo (persistir)**.
4. Observar el log estructurado aparecer y, con el refresh de 5 s, los KPIs
   (Total/PASS/BLOCK), la tabla de decisiones y la distribución por herramienta
   actualizarse con los nuevos eventos.
5. **🧹 Limpiar log** para comenzar una corrida limpia.

> Nota: cada corrida suma ~4 decisiones y ~1 evento a la DB (los 5 escenarios
> persisten sus registros). Es lo que se ve reflejado en los gráficos.