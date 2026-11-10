# Consideraciones generales del proyecto — ArgentGob-Mesh MVP

Este documento es el **punto de partida de cada sesión**. Debe estar accesible
para cualquier sesión nueva sobre el proyecto (Copilot, devs, CI). Contiene la
ubicación del plan normativo, los comandos de verificación (tests, E2E,
migraciones), la topología de contenedores Podman y las trampas recurrentes.

---

## 1. Plan general (fuente normativa)

Ubicación (NO está dentro del repo):

```
~/Downloads/mvp-plan-argentgob-v0.3-ampliado.md
```

En Windows (este equipo):
`C:\Users\FrancoYairBenko\Downloads\mvp-plan-argentgob-v0.3-ampliado.md`

Es la referencia normativa de hitos (H5–H11) y tracks (R). Para trabajar un
hito, hacer `grep -n '### <N> H'` o buscar la sección por título y leer su
"Entrada y unidades". **No cerrar un hito sin leer su "Cierre"/gate.**

---

## 2. Stack: Podman y topología de contenedores

Proyecto: `argentgob-mvp`. Repo/`compose.yaml` en el worktree que posee el
modelo: **`plan-implementacion-avancemos`** (= `main`).

> ⚠️ **Crítico**: `compose.yaml` bind-mountea `./models` relativo al CWD del
> compose. El modelo GGUF (4.6 GB) solo existe en el worktree
> `plan-implementacion-avancemos`. Si el stack se levanta desde otro worktree
> (p. ej. `h5-policy-engine-persistente-implementacion`, cuyo `models/` solo
> tiene `.gitkeep`), **`llama-server` entra en crash-loop** ("failed to open
> GGUF file"). **Siempre levantar el stack desde `plan-implementacion-avancemos`.**

Contenedores persistentes (running):
- `argentgob-mvp-postgres-1` — PostgreSQL 16 (named volume `argentgob-mvp_pgdata`,
  sobrevive a re-creación). Usuario/app: `argentgob_app` / `dev-password-local`,
  db `argentgob`. Solo es alcanzable como `postgres` **dentro** de la red
  `argentgob-mvp_default` (no publica `:5432` al host).
- `argentgob-mvp-dashboard-1` — Streamlit console :8501.
- `argentgob-mvp-llama-server-1` — llama-server (GGUF vía CUDA), health en
  `localhost:8080/health`.

Procesos on-demand (no persistentes): `agent` y `migrator` (se lanzan con
`podman run`).

---

## 3. Comandos de verificación

Todos usan la imagen `localhost/argentgob-mvp-agent:latest` con el código del
CWD montado (salvo que se indique rebuild). **Siempre** añadir
`-p no:cacheprovider` a `pytest`.

### 3.1 Suite de tests unitarios (completa)

```powershell
$src = (Get-Location).Path
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/ -q -p no:cacheprovider
```

Grep de los resumenes para conocer el baseline histórico:
baseline 71 → R4 +7 = 78 → R5 +8 +1 logger = 87 → H5 +15 = 102 → H6 +24 = 126
→ H7 +24 unit = 150 → H8 +42 = **192 passed** (unit).

> **Cifra oficial actual (verificada, AV-01):** la suite completa
> `pytest tests/` reporta **396 passed + 10 skipped** (2026-11-10). Los 10
> skipped son por ausencia de `TEST_DATABASE_URL` / `DATABASE_URL` (ver §7
> Límites): 7 de `test_postgres_real.py`, 2 de `test_dashboard_apptest.py` y 1
> de `test_agent_live_llm.py`.

### 3.2 Tests de integración

El runner es el mismo pero apuntando a `tests/integration`:

```powershell
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/integration -q -p no:cacheprovider
```

Actualmente: **60 passed + 7 skipped** (6 baseline R5 + 5 H7 + 5 H8 + 6 Fase 6
agentic flows + 8 perfiles/CrewAI + 2 concurrencia + 6 HITL E2E + 10 deriva +
8 políticas declarativas + 1 demo + 5 dashboard + 6 dashboard demo runner +
5 dashboard charts + 17 design system + …; los 7 skipped son `test_postgres_real`
sin `TEST_DATABASE_URL`).

### 3.3 Migraciones (Alembic)

Aplicar `upgrade head` (PostgreSQL alcanzable como `postgres` solo dentro de la
red del compose):

```powershell
podman run --rm --network argentgob-mvp_default `
  -e "DATABASE_URL=postgresql+psycopg://argentgob_app:dev-password-local@postgres:5432/argentgob" `
  -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
  python -m alembic upgrade head
```

Para verificar cabecera / estado:

```powershell
podman run --rm --network argentgob-mvp_default `
  -e "DATABASE_URL=postgresql+psycopg://argentgob_app:dev-password-local@postgres:5432/argentgob" `
  -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
  python -m alembic current
```

Estado actual: migraciones aplicadas hasta `007 (head)` (H8: roles de consola
`viewer`/`auditor`/`approver`/`admin`, vista `v_approval_console` sanitizada,
grants; capa predictiva: `risk_predictions` 005, `risk_evidence_view` 006 y
`model_drift_reports` 007).

### 3.3.1 Verificación de la cadena de auditoría (H7)

CLI que verifica la cadena desde el génesis (exit 0 solo si es íntegra):

```powershell
podman run --rm --network argentgob-mvp_default `
  -e "DATABASE_URL=postgresql+psycopg://argentgob_app:dev-password-local@postgres:5432/argentgob" `
  -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
  python -m argentgob.audit.cli
```

- Exit 0: `OK: la cadena de auditoría es íntegra.`
- Exit 1: `INVALID: cadena manipulada en sequence=N motivo=<sanitizado>`.
- Exit 2: error de infraestructura.

La cadena es **tamper-evident**, no inmutable: sin checkpoint externo no se
garantiza la detección de una reescritura completa o truncado final coherente
por owner/superusuario (límite declarado del MVP).

### 3.4 Verificación del stack (health checks)

```bash
./scripts/verify_stack.sh
```

Chequea: llama-server (`localhost:8080/health`), dashboard
(`localhost:8501/_stcore/health`) y postgres (`pg_isready`).

### 3.5 E2E: análisis de una empresa con logs (funciona)

Ejecución real del agente con persistencia en DB (red del compose + `DATABASE_URL`
apuntando a `postgres:5432`):

```powershell
$src = (Get-Location).Path
podman run --rm --network argentgob-mvp_default `
  -v "${src}:/app:Z" -w /app `
  -e OPENAI_API_BASE=http://llama-server:8080/v1 `
  -e OPENAI_API_KEY=local-no-key-required `
  -e OPENAI_MODEL_NAME=llama-3.1-8b-instruct `
  -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO `
  -e DATABASE_URL=postgresql+psycopg://argentgob_app:dev-password-local@postgres:5432/argentgob `
  argentgob-mvp-agent:latest python -m argentgob.agent.main AAPL --profile analyst
```

> El E2E con `--network host` NO escribe en DB porque postgres no publica
> `:5432` al host. Usar siempre `--network argentgob-mvp_default`.

### 3.6 Seguimiento de logs persistidos

Tablas útiles para el check post-E2E (consulta vía `psql` dentro del contenedor
postgres o con un runner):

- `governance_events` — eventos de gobernanza por tool (p. ej. news, stock_price).
- `governance_decisions` — decisiones correlacionadas (PASS/ALLOWED).
- `news_provider_attempt` (log) — intentos sanitizados de proveedor de noticias
  con correlación `event_id`, sin raw/bearer/headers.
- `policy_decisions`, `execution_results`, `audit_chain` (H5) — tablas del
  round-trip H5 y de la ejecución gobernada H6. En una corrida E2E permisiva
  (PASS) siguen en 0: `AuditWriter` persiste `governance_decisions`/
  `governance_events`, y `execution_results` se puebla por la ruta de ejecución
  gobernada (HITL/`ExecutionOrchestrator`) que requiere una aprobación humana.
- Vista `v_governance_console` — une `policy_decisions` + `execution_results`.

### 3.7 E2E: consola sanitizada y resolución autorizada (H8)

Script que verifica la consola H8 contra la DB real (métricas, lista
sanitizada, detalle HITL, resolución autorizada, manejo de carrera, bloqueo sin
evidencia y vista `v_approval_console`):

```powershell
$src = (Get-Location).Path
podman run --rm --network argentgob-mvp_default `
  -e "DATABASE_URL=******postgres:5432/argentgob" `
  -e "ENVIRONMENT=LOCAL" `
  -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
  python scripts/e2e_h8_console.py
```

Resultado esperado: `===== E2E H8: 23 passed, 0 failed =====`.

> **Quirk de `dict_row`**: `get_connection()` (psycopg3) devuelve filas tuple por
> defecto. Los servicios H8 (`ConsoleQueryService`, `HitlDetailService`) usan
> `conn.cursor(row_factory=dict_row)` para acceder por nombre de columna. Los
> tests unitarios usan fakes con `cursor(self, row_factory=None)`.

---

## 4. Convención de branches / git

- **Al iniciar cada sesión de desarrollo**, crear una rama de trabajo nueva para
  ese hito (prefijo `agents/`); si ya existe, `git switch` a ella. El trabajo de
  la sesión se hace siempre en esa rama.
- El worktree que mantiene el **checkout de `main`** es `plan-implementacion-avancemos`.
  No se puede `git checkout main` desde otro worktree. El merge a `main` se hace:
  ```powershell
  git -C "<plan-implementacion-avancemos>" merge --ff-only <branch>
  ```
- Al cierre de la sesión (cambios validados con tests + E2E): (1) push de la
  rama de trabajo (`git push -u origin <branch>`) y (2) merge `--ff-only` a
  `main` desde el worktree raíz.
- Branches de trabajo por hito, prefijo `agents/`, p. ej.
  `agents/h5-policy-engine-persistente-implementacion`.
- Regla de release: **no se pushea ni mergea hasta que pasen la suite unitaria y
  el test integral** (E2E del caso).
- Commits con prefijo convencional (`feat:`, `fix:`, `docs:`, `test:`). Añadir
  el trailer:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```
- Config SSH necesaria para push/fetch:
  ```bash
  git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe'
  ```
  **Usar forward slashes.**

---

## 5. Trampas recurrentes (quirks)

- **PowerShell `NativeCommandError`**: podman/git escriben en stderr y PS reporta
  "exit 1" aparente aunque el comando tenga éxito. Redirigir a archivo
  (`> out.log 2>&1`) y comprobar `$LASTEXITCODE` / contenido del archivo.
- **Git protegido por Vault Radar**: los commits imprimen advertencias a stderr
  (exit 1) pero **el commit sí se realiza**. Verificar con `git log`.
- **Mojibake en la terminal**: los archivos son UTF-8 válidos; la consola muestra
  acentos mal. Hacer match de substrings **sin acentos**.
- **El tool `edit` mangla indentación Python multilínea**: preferir reescritura
  byte-exacta vía Python (`io.open().read() → replace → write`) y luego
  `py_compile` + `view`.
- **`Set-Content -Encoding UTF8` añade BOM**: usar
  `[System.IO.File]::WriteAllText(path, txt, (New-Object System.Text.UTF8Encoding($false)))`.
- **Imagen de tests vs código local**: los cambios en `src/` NO se aplican en la
  imagen de tests sin rebuild; pero el E2E con `-v "${PWD}:/app"` usa el código
  local directamente.
- **`__pycache__`** son dirs gitignoreados (untracked).

---

## 6. Estado alcanzado

- Track R (R1–R5): **completo**.
- H5 — Policy Engine persistente: **completo** (migración 002 aplicada, 15 tests
  propios, merge a `main` en `d74376f`).
- H6 — Aprobación humana durable y ejecución gobernada: **completo** (paquete
  `src/argentgob/hitl/`, hook HITL durable en el PEP, crew con
  `DeterministicEvaluator`/`PolicyStore` + servicios HITL, 24 tests propios →
  baseline 126 passed). Merge a `main` en `0bbe158`.
- H7 — Auditoría detectable ante manipulación: **completo** (paquete
  `src/argentgob/audit/` con `canonical`/`append`/`verify`/`cli`, migración 003
  con trigger append-only + grants + columna `payload`, 24 tests unit + 5
  integración → baseline 161 passed). Merge a `main` en `87daf67`.
- H8 — Consola sanitizada y resolución autorizada: **completo** (paquetes
  `src/argentgob/console/` y `src/argentgob/identity/`, migración 004 con roles
  + vista `v_approval_console`, extensión del dashboard Streamlit con la consola
  H8, 42 tests nuevos → baseline **203 passed** (192 unit + 11 integración)).
  E2E `scripts/e2e_h8_console.py`: 23/23 checks OK contra la DB real. Merge a
  `main` en `e923f8d`.
- Capa predictiva (H12–H16, Fases 1–6 del [plan de continuación](plan-continuacion.md)):
  **completo** — corpus sintético `corpus_v1` (3000 escenarios, seed 42),
  modelo `risk_model_v1` (regresión logística calibrada, Macro F1 0.9985,
  Recall HIGH 1.0), integración en el `pre_hook` del PEP con precedencia
  `BLOCK > HIGH(BLOCK) > MEDIUM(HITL) > LOW > fallback fail-closed`, persistencia
  `risk_predictions`/`risk_model_versions` (migración 005) y vista
  `v_risk_evidence` (006). E2E Fase 4: 6/6, Fase 5: 19/19.
- Prioridad alta del hito: **completo** — criterios de aceptación H1–H4/H8–H11
  (formato 3 Cs en `modulo2-entrega.md` §3.2), HITL verificado end-to-end
  (`tests/integration/test_hitl_end_to_end.py`: predicción MEDIUM → approval →
  resolución → resume → ejecución gobernada; demo `scripts/demo_end_to_end.py`
  escenario 5), tests de PostgreSQL real (`test_postgres_real.py`, se salta sin
  `TEST_DATABASE_URL`), concurrencia y CrewAI real.
- Prioridad media: **completo** — monitoreo de deriva (PSI, migración 007
  `model_drift_reports`, sección "Salud del Modelo" del dashboard); políticas
  declarativas YAML como fuente canónica (`policies/*.yaml` → seed con UPSERT +
  pruning, `sensitivity_limit` ejercido, comodín `*`); demo end-to-end de 5
  escenarios (`scripts/demo_end_to_end.py`, reproducible en memoria o
  PostgreSQL).
- Rediseño UX/UI del dashboard (prompt v4): **completo** — 5 vistas
  multipágina (`st.navigation` + `st.Page`), design system (`.streamlit/config.toml`,
  `palette.py`, `charts.py` con ECharts puros, `queries.py`, `components.py`),
  KPIs neutros, heatmap Predicción×Decisión, matriz de confusión y curva de
  calibración, demo ejecutable desde la UI. Suite completa **334 passed + 9
  skipped**. Merge a `main` en `e923f8d`.
- Estado del repositorio: ver §8 (Relevamiento del repositorio).
- **AV-00 — Baseline operativo y contratos: completo (PASS)** — verificado el
  2026-11-10 sobre `f81bd7d`: suite **334 passed + 9 skipped**, PG real 7/7,
  corpus OK, E2E Fase 4 6/6, Fase 5 19/19, H8 23/23 sobre DB limpia, cadena de
  auditoría íntegra, demo determinista 5 escenarios y smoke LLM real con
  inferencia y tools. Se mapearon los contratos a rutas reales y se registraron
  seis brechas que condicionan AV-02/04/05 (seed ausente en el inicializador,
  revalidación incompleta en resume, cadena de auditoría no cableada, sin
  correlación de run, sin persistencia de liberación de contenido e identidad
  HITL sin configurar). Detalle en
  [sesión 0025](sessions/0025-resumen-av-00-baseline-contratos.md).
- **AV-01 — Servicio HTTP, supervisor y eventos: completo (PASS)** — verificado
  el 2026-11-10: suite **396 passed + 10 skipped**, gate
  `scripts/verify_agent_live.py --milestone AV-01 --with-llm` PASS (84 tests +
  1 smoke LLM real), `scripts/api_check_agent_live.py` 16/16 contra el servicio
  desplegado, PG real 7/7, H8 23/23 sobre DB aislada, Fase 4 6/6, Fase 5 19/19,
  corpus OK, demo determinista 5 escenarios y CLI financiera con LLM real.
  Implementa `argentgob.live` (API FastAPI, supervisor con un proceso hijo por
  run, IPC por socket loopback autenticado, buffer de eventos con allowlist) y
  cierra la brecha de correlación de run (`run_id` → `session_id`). Detalle en
  [sesión 0026](sessions/0026-resumen-av-01-servicio-supervisor-eventos.md).
- Próximo hito: **AV-02 — Compose e inicialización reproducibles** (ver
  [hitos](implementacion-agente-vivo/hitos.md)). AV-02…AV-06 siguen pendientes.

---

## 7. Límites declarados del prototipo

> Sección solicitada en el cierre de brechas para dejar explícitos los límites
> metodológicos y operacionales del MVP, de modo que la documentación de la
> tesis no presente el prototipo como si validara más de lo que valida.

### 7.1 Corpus sintético y validez externa del modelo predictivo

- El corpus `corpus_v1` (3000 escenarios, seed 42) es **100 % sintético**,
  generado por reglas (`scripts/build_synthetic_corpus.py`); no contiene PII
  real (validado: `validation_report.json`).
- Las métricas del modelo (`risk_model_v1`, regresión logística calibrada) son
  **casi perfectas** (Macro F1 0.9985, Recall HIGH 1.0) precisamente porque el
  modelo aprende la rúbrica de etiquetado del corpus sintético.
- **Advertencia metodológica (validez externa):** el desempeño sobre tráfico
  real etiquetado por humanos **no está demostrado**; requiere un corpus real
  para afirmar generalización. Esto queda como trabajo futuro (ver
  `docs/analytics/0003-arquitectura-y-evidencia.md` §5).
- Los tests de software (unit + integración) y los E2E **no sustituyen** la
  evaluación analítica/predictiva: son tres tipos de evidencia distintos y así
  deben presentarse en la tesis (tests de software / métricas analíticas de
  detectores / métricas predictivas del modelo).

### 7.2 HITL verificado con fixtures, no con auditor humano real

- El flujo HITL end-to-end está verificado mediante **fixtures y componentes
  reales** (predicción MEDIUM → approval PENDING → token de reanudación 1-uso →
  resolución APPROVED → resume → ejecución gobernada), en
  `tests/integration/test_hitl_end_to_end.py` y en el escenario 5 de
  `scripts/demo_end_to_end.py`.
- **No** se realizó una corrida con un **auditor humano real** operando la
  consola en producción. La resolución autorizada está validada con identidad y
  evidencia (H8), pero la operación con personas queda como validación
  operacional.
- La tasa de HITL originada por predicción depende de la distribución de
  tráfico real (no hay datos de producción).

### 7.3 Tests skipped por falta de `TEST_DATABASE_URL` / `DATABASE_URL`

- La suite completa reporta **396 passed + 10 skipped**. Los **10 skipped** son
  exactamente:
  - `tests/integration/test_postgres_real.py` (7): validan migraciones en head,
    constraints UNIQUE/CHECK, grants mínimos de roles de consola y flujo real
    LOW→PASS contra una base PostgreSQL de **prueba**; se saltan cuando
    `TEST_DATABASE_URL` no está definida.
  - `tests/unit/test_dashboard_apptest.py` (2): cargan el dashboard Streamlit
    (AppTest) contra la base; se saltan sin `DATABASE_URL` (stack PostgreSQL).
  - `tests/integration/test_agent_live_llm.py` (1): smoke LLM real de AV-01;
    se salta sin `DATABASE_URL` y sin llama.cpp alcanzable. Se acredita con
    `-m llm_local` sobre la red del stack.
- Para acreditarlos en una corrida de cierre:
  ```powershell
  podman run --rm -e "TEST_DATABASE_URL=******postgres:5432/argentgob_test" `
    -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
    pytest tests/integration/test_postgres_real.py -q -p no:cacheprovider
  ```
- Los E2E contra PostgreSQL real (`e2e_h8_console`, `e2e_fase4_predictive`,
  `e2e_fase5_evidence`) sí requieren el stack levantado y la red
  `argentgob-mvp_default`.

### 7.4 Otros límites declarados

- **Cadena de auditoría tamper-evident, no inmutable:** sin checkpoint externo
  no se garantiza la detección de una reescritura completa o truncado final
  coherente por owner/superusuario (ver §3.3.1).
- **Detector PII:** sanitización y guardrails son **deterministas** (regex);
  no existe "reconocimiento probabilístico" de PII. El detector probabilístico
  de PII está fuera de alcance (backlog).
- **Modelo GGUF:** `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` (~4.9 GB) solo
  existe en el worktree raíz (`main`); otros worktrees con `models/` vacío
  hacen crash-loop en `llama-server` (ver §2).

---

## 8. Relevamiento del repositorio (estado verificado)

> Sección solicitada en el cierre de brechas: resumen del estado del
> repositorio verificado al 2026-11-09 (sesión de cierre de brechas, HEAD
> `e923f8d` en `main`, árbol limpio).

### 8.1 Funcionalidades (H1–H16)

| ID | Funcionalidad | Estado | Evidencia |
|---|---|---|---|
| H1 | Envelope canónico de tool call | ✅ Implementada y verificada | `src/argentgob/core/envelope.py`, INV-01/INV-15 |
| H2 | Sanitización de PII y truncado (regex deterministas) | ✅ Implementada y verificada | `src/argentgob/module_b/sanitizer.py`, INV-04/INV-05 |
| H3 | Evaluación ABAC por perfil con fallback in-memory | ✅ Implementada y verificada | `src/argentgob/module_c/abac_evaluator.py` |
| H4 | Guardrails de dominio (inyección, relevancia, rate limit) | ✅ Implementada y verificada | `src/argentgob/module_c/guardrails.py` |
| H5 | Policy engine persistente (versión, matching, precedencia) | ✅ Implementada y verificada | migración 002, `PolicyStore`, `policy_decisions` |
| H6 | Aprobación humana HITL (durable, tokens 1-uso) | ✅ Implementada y verificada E2E (fixtures) | `src/argentgob/hitl/`, `test_hitl_end_to_end.py`, demo escenario 5 |
| H7 | Auditoría encadenada tamper-evident + verificador | ✅ Implementada y verificada | migración 003, `src/argentgob/audit/`, CLI exit 0/1/2 |
| H8 | Consola sanitizada y resolución autorizada | ✅ Implementada y verificada | migración 004, `src/argentgob/console/` + `identity/`, E2E 23/23 |
| H9 | Integración con CrewAI (GovernedTool) | ✅ Implementada y verificada | `src/argentgob/module_a/governed_tool.py`, `test_crewai_real.py` |
| H10 | Dashboard de monitoreo (5 vistas, ECharts, auto-refresh) | ✅ Implementada y verificada | `src/argentgob/dashboard/`, AppTest, browser |
| H11 | Suite de tests unitarios e integración | ✅ Implementada (**396 passed + 10 skipped**) | `pytest tests/` (verificado 2026-11-10, AV-01) |
| H12 | Corpus predictivo versionado (sintético) | ✅ Implementada y verificada | `data/corpus/corpus_v1.parquet` + `corpus_manifest.json` (seed 42, hash congelado), `validate_corpus.py` OK |
| H13 | Preparación de features (27, pre-ejecución, anti-leakage) | ✅ Implementada y verificada | `src/argentgob/analytics/features.py` (FeatureBuilder) |
| H14 | Entrenamiento/calibración/evaluación del modelo | ✅ Implementada y verificada | `scripts/train_risk_model.py`, `data/models/risk_model_v1/` (pipeline.joblib, thresholds.json, metrics) |
| H15 | Inferencia de riesgo pre-ejecución + persistencia | ✅ Implementada y verificada | `src/argentgob/analytics/predictor.py` + `store.py`, migración 005, E2E Fase 4 6/6 |
| H16 | Integración con PASS/BLOCK/HITL + dashboard + evidencia | ✅ Implementada y verificada | `v_risk_evidence` (migración 006), dashboard "Inteligencia Predictiva", E2E Fase 5 19/19 |

### 8.2 Migraciones aplicadas

`001 → 002 → 003 → 004 → 005 → 006 → 007 (head)` — verificable con
`python -m alembic current` (`007 (head)`).

| Migración | Contenido |
|---|---|
| 001 | Schema inicial R0 (`governance_policies`, `governance_decisions`, `governance_events`) |
| 002 | Policy engine H5 + HITL H6 (`policy_decisions`, `execution_results`, `approval_requests`, `approval_resume_tokens`) |
| 003 | Auditoría H7 (`audit_chain` + trigger append-only + owner dedicado) |
| 004 | Consola H8 (roles `viewer`/`auditor`/`approver`/`admin`, vista `v_approval_console`) |
| 005 | Capa predictiva Fase 4 (`risk_model_versions`, `risk_predictions`) |
| 006 | Vista `v_risk_evidence` (Fase 5, 17 columnas sanitizadas) |
| 007 | Monitoreo de deriva (`model_drift_reports`, PSI) |

### 8.3 Verificaciones (E2E y suite ejecutadas en el cierre de brechas)

| Verificación | Resultado |
|---|---|
| `pytest tests/` (suite completa, imagen reconstruida) | **396 passed + 10 skipped** (AV-01, 2026-11-10) |
| E2E H8 (`scripts/e2e_h8_console.py`) | 23/23 checks OK (sobre DB de prueba limpia; 22/23 sobre la DB compartida por acumulación de approvals PENDING) |
| E2E Fase 4 (`scripts/e2e_fase4_predictive.py`) | 6/6 checks OK |
| E2E Fase 5 (`scripts/e2e_fase5_evidence.py`) | 19/19 checks OK |
| Demo end-to-end (`scripts/demo_end_to_end.py`, 5 escenarios) | OK / BLOCK (`PREDICTED_HIGH_RISK`, `INJECTION_PATTERN_DETECTED`, `SENSITIVITY_EXCEEDED`) / HITL COMPLETO |
| `python -m argentgob.audit.cli` | `OK: la cadena de auditoría es íntegra.` |

### 8.4 Árbol del repositorio y ramas

- Rama activa: `main` (worktree raíz `plan-implementacion-avancemos`, con el
  modelo GGUF). HEAD `e923f8d` (rediseño UX/UI v4), árbol limpio.
- Branches de trabajo históricas (`agents/*`) en
  `abacus-tesis-mvp.worktrees/` — todas ya mergeadas a `main` (ver §4).
- Artefactos versionados: `data/corpus/` (corpus + manifiesto + reporte),
  `data/models/risk_model_v1/` (pipeline, umbrales, metadata, resultados),
  `policies/*.yaml` (fuente canónica), `prompts/*.md`, `migrations/versions/`.

### 8.5 Notas de trazabilidad (desde la revisión externa)

- Código-en-rama vs código-en-imagen vs reproducible-desde-repo: todo lo
  funcional está en `main` y es reproducible desde el repo (imagen
  `localhost/argentgob-mvp-agent:latest` reconstruible con
  `containers/agent.Containerfile`); la rama histórica
  `feat/r0-reconciliation-baseline` contenía solo el alcance R0.
- Las inconsistencias de la revisión externa quedaron cerradas: criterios de
  aceptación (3 Cs) en `modulo2-entrega.md` §3.2; backlog 81 PH + 13 PH
  operativas = sprints 94 PH (§3.3); H7 asignado solo a Sprint 2; "reconocimiento
  probabilístico de PII" aclarado como inexistente; tres tipos de evidencia
  separados en `docs/analytics/0003-arquitectura-y-evidencia.md` §4.

---

## 9. Detalle de creación del corpus predictivo

> Fuente normativa: `scripts/build_synthetic_corpus.py` (Fase 2 del plan de
> continuación) alineado con `docs/analytics/0001-dataset-specification.md`,
> `0002-labeling-guide.md` y `risk_dataset_schema.json` (feature_schema_v1).

### 9.1 Objetivo y unidad de análisis

- **Unidad de observación:** tentativa de tool call **antes** de su ejecución
  (el `ToolCallEnvelope` del pre-hook).
- **Problema:** clasificación supervisada multiclase del riesgo de una tool
  call (`LOW` / `MEDIUM` / `HIGH`).
- **Anti-circularidad:** la etiqueta se asigna por **rúbrica humana** sobre
  características observables (datos, operación, recurso, contenido, control);
  **nunca** copia `observed_action` ni la salida del motor determinista. El
  `label_rationale` apunta al eje/evidencia aplicado (auditable).

### 9.2 Generación (parámetros y determinismo)

| Parámetro | Valor |
|---|---|
| Script | `scripts/build_synthetic_corpus.py` |
| Semilla | `--seed 42` (reproducible: misma seed ⇒ mismo corpus y `evaluation_freeze_hash`) |
| Tamaño | `--size 3000` escenarios |
| Particiones | 60 % train (1800) / 20 % calibration (600) / 20 % evaluation congelada (600), estratificado por clase |
| Congelamiento | `evaluation_partition_frozen: true` + `evaluation_freeze_hash = sha256:c567c445…` (no se regenera sin cambiar `corpus_version`) |

### 9.3 Dominios cubiertos (alineados con el código real)

- **Herramientas:** `news`, `stock_price`, `crypto_price`.
- **Clases de operación:** `READ`, `EXTERNAL_SEND`, `WRITE`, `DELETE`, `DDL`,
  `CODE_EXEC`.
- **Perfiles/roles:** `analyst`, `analyst_restricted`, `admin`.
- **Entornos:** `LOCAL`, `TEST`, `PROD`.
- **Recursos por herramienta:** p. ej. `duckduckgo.com`, `yahoo_finance`,
  `market_data_api`, `coinmarketcap`, `binance_public`.

### 9.4 Tipos de escenario y rúbrica de etiquetado

| Categoría | Rúbrica (eje) | Etiqueta típica | Ejemplos en `build_synthetic_corpus.py` |
|---|---|---|---|
| **Benigno** | operación/contenido (lectura pública, sin PII ni patrones) | `LOW` | consulta de precios con ticker, noticias con términos financieros |
| **Maligno** | contenido/datos/operación (inyección, exfiltración, escritura destructiva) | `HIGH` | `DROP TABLE…`, `xp_cmdshell`, exfiltración EMAIL+PAYMENT_NUMBER, `rm -rf`, host no permitido |
| **Límite** | datos/operación/contenido (PII aislada, envío externo, estructura anidada) | `MEDIUM` | `POSSIBLE_DNI` en recurso permitido, `EXTERNAL_SEND` a host conocido, JSON profundamente anidado |
| **Conflicto** | control (políticas con obligaciones incompatibles) | `MEDIUM`/`HIGH` | conflicto de políticas, `sensitivity_limit` excedido |

> La **PII sintética** usa **placeholders** (`cliente.ejemplo@correo.test`,
> `12345678`, `4111111111111111`); el detector `_is_pii_real` distingue
> sintética de real (dominio `.test` y PAN/DNI de ejemplo no cuentan).

### 9.5 Composición y validación del corpus (manifiesto `corpus_manifest.json`)

| Métrica | Valor |
|---|---|
| Filas | 3000 (registros únicos: 3000; perfiles de features únicos: 2848; 152 filas duplicadas por features permitido por diseño: hora/args) |
| Etiquetas | LOW 1500 (50 %) / MEDIUM 900 (30 %) / HIGH 600 (20 %) |
| Fuente de escenarios | benigno 1477 / límite 720 / maligno 580 / conflicto 223 |
| Herramientas | news 1486 / stock_price 951 / crypto_price 563 |
| Perfiles | analyst 1684 / admin 838 / analyst_restricted 478 |
| Splits | train 1800 / calibration 600 / evaluation 600 |
| Nulos / NaN / requeridos faltantes | 0 |
| PII real detectada | `[]` (ninguna) |
| Balance y ≥15 % límite/conflicto | `balance_ok: true`, `min_15pct_boundary_conflict: true` |

Validador: `scripts/validate_corpus.py` → **`RESULTADO: OK`** (valida contra
`risk_dataset_schema.json` + controles, exit 0/1). Tests:
`tests/unit/test_corpus.py` (8 tests: esquema, controles, PII, balance, split,
dominios, congelado, anti-circularidad).

### 9.6 Artefactos versionados

| Artefacto | Ruta | Contenido |
|---|---|---|
| Corpus Parquet | `data/corpus/corpus_v1.parquet` | 3000 filas con features + target + metadatos |
| Manifiesto | `data/corpus/corpus_manifest.json` | versión, seed, split, hash congelado, validación |
| Reporte de validación | `data/corpus/validation_report.json` | PII real, unicidad, completitud, dominio, balance |
| Generador | `scripts/build_synthetic_corpus.py` | reproducible (`--seed 42 --size 3000`) |
| Validador | `scripts/validate_corpus.py` | exit 0/1 contra schema + controles |

---

## 10. Relevamiento consolidado — Sesiones `docs/sessions/` vs. revisión externa

> Resultado del relevamiento documental de las sesiones 0001–0022 (realizado
> con 4 subagentes de lectura en paralelo) para resolver las observaciones de
> la revisión externa de la tesis (revisor académico). Fecha: 2026-11-09.

### 10.1 Matriz de resolución de las observaciones del revisor

| # | Observación del revisor | Veredicto hoy | Evidencia en `docs/sessions/` |
|---|---|---|---|
| 1 | Criterios de aceptación faltantes de H1–H4 y H8–H11 | ✅ Resuelto — formato 3 Cs (Dado/Cuando/Entonces) | `0016` + `modulo2-entrega.md` §3.2 |
| 2 | HITL sin verificación end-to-end | ✅ Resuelto — appraisal MEDIUM → PENDING → APPROVED → resume → ejecución gobernada | `0016` (`test_hitl_end_to_end.py`), `0020` (escenario 5 HITL COMPLETO) |
| 3 | Relaciones y cardinalidades sin detallar | ✅ Resuelto — 11+ relaciones con cardinalidades e invariantes | `0016` + `modulo2-entrega.md` §3.5 + `analytics/0003` §2.2 |
| 4 | Sprints sin descomponer en tareas | ✅ Resuelto — 4 sprints (94 PH) + cierre (9 PH) con tareas/esfuerzo/dependencias | `0016` + `modulo2-entrega.md` §3.3 |
| 5 | Componente predictivo ausente (H12–H16) | ✅ Resuelto — Fases 1–6 implementadas (corpus, 27 features anti-leakage, modelo logístico calibrado, inferencia pre_hook, precedencia BLOCK>HIGH>MEDIUM(HITL)>LOW>fallback) | `0010`–`0015` |
| 6 | Backlog 82 PH vs sprints 84 PH | ✅ Aclarado — hoy backlog 81 PH + 13 PH operativas = sprints 94 PH | `0016` + `modulo2-entrega.md` §3.1/§3.3 |
| 7 | H7 en Sprint 2 y 3 a la vez | ✅ Corregido — H7 solo en Sprint 2 | `modulo2-entrega.md` §3.3 |
| 8 | "Reconocimiento probabilístico" de PII sin evidencia | ✅ Aclarado — no existe; sanitización 100 % regex determinista | Fase 0 (`plan-continuacion.md` §1.1) + `0002` |
| 9 | Código-en-rama vs imagen vs reproducible | ✅ Resuelto — `main` reproducible; rama histórica solo R0 | Nota de trazabilidad en `modulo2-entrega.md` |
| 10 | Separar evidencia: tests / métricas analíticas / métricas predictivas | ✅ Resuelto — tres secciones separadas | `0014` + `analytics/0003` §4.1–4.3 |

### 10.2 Estado consolidado del prototipo (por los 4 subagentes + verificación)

- **Hitos completos:** R1–R5, H1–H8, capa predictiva Fases 1–6 (H12–H16),
  prioridad alta (criterios, HITL E2E, PG real, concurrencia, CrewAI real),
  prioridad media (deriva PSI, políticas YAML, demo E2E 5 escenarios) y
  rediseño UX/UI v4.
- **Tests:** suite completa verificada **396 passed + 10 skipped** (ver §3.1).
- **Migraciones:** 001 → **007 (head)** (ver §8.2).
- **E2E:** H8 23/23 · Fase 4 6/6 · Fase 5 19/19 · demo 5/5 escenarios.
- **Dashboard:** 5 vistas multipágina (ver §10.3).

### 10.3 Capturas del dashboard (todas las secciones)

> Carpeta: `docs/dashboard-captures/` (capturas tomadas el 2026-11-09 contra el
> stack real, `localhost:8501`).

| Vista | Archivo |
|---|---|
| Executive Overview | [01-executive-overview.png](dashboard-captures/01-executive-overview.png) |
| Auditoría y HITL | [02-auditoria-hitl.png](dashboard-captures/02-auditoria-hitl.png) |
| Inteligencia Predictiva | [03-inteligencia-predictiva.png](dashboard-captures/03-inteligencia-predictiva.png) |
| Salud del Modelo | [04-salud-modelo.png](dashboard-captures/04-salud-modelo.png) |
| Operaciones y Demo | [05-operaciones-demo.png](dashboard-captures/05-operaciones-demo.png) |

---

## 11. Documentos relacionados

- **[Plan v3 de agente en vivo y escenarios públicos](implementacion-agente-vivo/README.md)** — hoja de ruta de la ampliación AV-00…AV-06. AV-00 y AV-01 están completos con gate PASS ([0025](sessions/0025-resumen-av-00-baseline-contratos.md), [0026](sessions/0026-resumen-av-01-servicio-supervisor-eventos.md)); AV-02…AV-06 siguen pendientes. Incluye contratos, perfiles/guardrails, configuración, demos con LLM real y tests/gates por hito.
- [Plan de continuación (hoja de ruta predictiva histórica)](plan-continuacion.md)
- [Entrega del Módulo 2 (documentación técnica e ingeniería)](modulo2-entrega.md)
- [Resúmenes de sesión por hito](sessions/)
- [Arquitectura y evidencia de la capa predictiva](analytics/0003-arquitectura-y-evidencia.md)
- [Análisis de tests de integración (sesgos Fase 6)](analytics/0004-analisis-tests-integracion.md)