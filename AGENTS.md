# ArgentGob-Mesh — Instrucciones de sesión

> **Proyecto de tesis.** Toda la documentación está en español.
> Estas instrucciones se aplican en **cada sesión** de desarrollo del prototipo
> ArgentGob-Mesh: un middleware de gobernanza perimetral para agentes CrewAI en
> el dominio financiero.

---

## 1. Dónde empezar cada sesión

**Siempre** leer primero estos documentos para conocer el estado y las
convenciones antes de tocar código:

| Documento | Propósito |
|---|---|
| [`docs/PROJECT_CONSIDERATIONS.md`](docs/PROJECT_CONSIDERATIONS.md) | **Punto de partida obligatorio**: estado alcanzado, comandos de verificación, topología Podman, trampas recurrentes, convención de branches |
| [`docs/implementacion-agente-vivo/README.md`](docs/implementacion-agente-vivo/README.md) | **Hoja de ruta vigente de esta ampliación (v3)**: despliegue, agente en vivo y cinco contextos públicos; todo AV pendiente hasta evidencia de cierre |
| [`docs/implementacion-agente-vivo/hitos.md`](docs/implementacion-agente-vivo/hitos.md) | Entrada, unidades, DAG, tests, gates y entregables de AV-00…AV-06; leer el hito activo antes de editar |
| [`docs/implementacion-agente-vivo/contratos.md`](docs/implementacion-agente-vivo/contratos.md) | Flujos, API, inputs/outputs, eventos, identidad, HITL, PII/IPI e invariantes |
| [`docs/implementacion-agente-vivo/escenarios.md`](docs/implementacion-agente-vivo/escenarios.md) | Perfiles, tools, políticas, guardrails y demos D-01…D-12 con LLM real |
| [`docs/implementacion-agente-vivo/despliegue.md`](docs/implementacion-agente-vivo/despliegue.md) | Configuración por servicio, límites, inicialización y degradación |
| [`docs/implementacion-agente-vivo/verificacion.md`](docs/implementacion-agente-vivo/verificacion.md) | Oráculos T-*, runners actuales/futuros, evidencia y checklist de cierre |
| [`docs/plan-continuacion.md`](docs/plan-continuacion.md) | Hoja de ruta predictiva histórica (Fases 0–6); no es el backlog de la nueva ampliación |
| [`docs/sessions/`](docs/sessions/) | Evidencia por sesión; consultar las más recientes aplicables, especialmente 0022 y 0023 |
| [`docs/modulo2-entrega.md`](docs/modulo2-entrega.md) | Entrega académica histórica; sus cifras no sustituyen evidencia reciente |

**Plan normativo de hitos** (NO está en el repo, está en `~/Downloads/`):
`mvp-plan-argentgob-v0.3-ampliado.md`. Para trabajar un hito, buscar su sección
por título y leer su "Entrada y unidades" y su "Cierre"/gate. **No cerrar un
hito sin leer su gate.**

---

## 2. Estado actual del prototipo (baseline)

- **Hitos completos:** H1–H8 (intercepción, sanitización, ABAC, guardrails,
  policy engine persistente, HITL, auditoría encadenada, consola sanitizada)
  y Fases 1–6 de la capa predictiva (definición analítica, corpus sintético,
  modelado, integración operacional, interfaz y evidencia académica, tests de
  integración de flujos agénticos).
- **Última suite documentada:** **334 passed + 9 skipped**, reconfirmada el 2026-11-10 en AV-00 ([0025](docs/sessions/0025-resumen-av-00-baseline-contratos.md)); los 9 skips son 7 de `test_postgres_real.py` (sin `TEST_DATABASE_URL`) y 2 de `test_dashboard_apptest.py` (sin `DATABASE_URL`). Deriva, modelo canónico y demo determinista de cinco escenarios están verificados.
- **Migraciones presentes:** 001 → **007**; última revisión aplicada documentada: `007 (head)` en [consideraciones](docs/PROJECT_CONSIDERATIONS.md). Consultar DB real antes de afirmar revisión aplicada actual.
- **Dashboard:** cinco vistas multipágina del rediseño v4: Executive Overview, Auditoría y HITL, Inteligencia Predictiva, Salud del Modelo y Operaciones y Demo ([0022](docs/sessions/0022-resumen-rediseno-v4.md)). La demo actual no usa LLM; `--real` significa persistencia en PostgreSQL.
- **LLM local:** GPU/CDI y E2E financiero real documentados en [0023](docs/sessions/0023-resumen-llama-server-gpu-e2e-agente-real.md). No asumir disponibilidad actual ni que `test_crewai_real.py` invoque llama.cpp: prueba tools CrewAI con proveedor fake.
- **Todavía propuesto:** API persistente del agente, timeline incremental, cinco dominios públicos, fronteras de resultados PII/IPI y resume interactivo vinculado al worker. No presentar estos elementos del plan v3 como implementados.
- **E2E H8:** `scripts/e2e_h8_console.py` → 23/23 checks OK.
- **E2E Fase 4:** `scripts/e2e_fase4_predictive.py` → 6/6 checks OK.
- **E2E Fase 5:** `scripts/e2e_fase5_evidence.py` → 19/19 checks OK.
- **Checkout base con GGUF:** raíz del repositorio; consultar `git status`, rama y worktrees al iniciar. Trabajar los cambios en `agents/…`, no asumir que la rama activa es `main`.

### Próximo hito

La capa predictiva Fases 0–6 y las mejoras posteriores están documentadas; no volver a tratar los pendientes históricos ya resueltos como backlog nuevo sin contrastar sesiones. **AV-00 (baseline operativo y contratos) está completo con gate PASS** ([0025](docs/sessions/0025-resumen-av-00-baseline-contratos.md)). El próximo hito es **AV-01: servicio HTTP, supervisor y eventos**, según [hitos](docs/implementacion-agente-vivo/hitos.md). AV-01…AV-06 están **pendientes**: redactar documentación no cierra sus gates.

Brechas verificadas en AV-00 que condicionan hitos posteriores (no presentarlas como resueltas): el inicializador no siembra políticas (AV-02), `GovernedResumeService._revalidate` no revalida política/digest/guardrails (AV-05), `AuditChainAppender` no está cableado al runtime (AV-05), no hay correlación de run (AV-01), no hay persistencia de liberación de contenido (AV-04) y la identidad HITL no está configurada en el despliegue (AV-02). Detalle en [0025](docs/sessions/0025-resumen-av-00-baseline-contratos.md).

Los IDs AV no sustituyen ni renumeran H1–H16. Ante conflicto, preservar invariantes y plan normativo externo, registrar la decisión y bloquear solo la unidad afectada. No inventar contratos para acomodar nombres de archivos propuestos.

---

## 3. Stack Podman (despliegue y pruebas)

Todo el código se despliega y prueba **dentro de contenedores Podman**. El
proyecto compose se llama `argentgob-mvp`.

### Imágenes

| Imagen | Uso |
|---|---|
| `localhost/argentgob-mvp-agent:latest` | Agente + middleware + **tests** (incluye `.[test]`) |
| `localhost/argentgob-mvp-agent-h7:latest` | Imagen H7 (histórica) |
| `docker.io/library/argentgob-mvp-migrator:latest` | Migraciones Alembic |
| `docker.io/library/argentgob-mvp-dashboard:latest` | Dashboard Streamlit |
| `ghcr.io/ggml-org/llama.cpp:server-cuda` | LLM local (llama-server) |
| `docker.io/library/postgres:16-alpine` | PostgreSQL |

### Contenedores persistentes

- `argentgob-mvp-postgres-1` — PostgreSQL 16 (volumen `argentgob-mvp_pgdata`).
  Alcanzable como `postgres` **solo dentro** de la red `argentgob-mvp_default`
  (no publica `:5432` al host).
- `argentgob-mvp-dashboard-1` — Streamlit `:8501`.
- `argentgob-mvp-llama-server-1` — llama-server (health en `localhost:8080/health`).

Procesos on-demand (no persistentes): `agent` y `migrator` (se lanzan con
`podman run`).

### Red

`argentgob-mvp_default` (bridge). **Los E2E que escriben en DB deben usar
`--network argentgob-mvp_default`**; `--network host` NO escribe en DB.

### ⚠️ Crítico: worktree del modelo

`compose.yaml` bind-mountea `./models` relativo al CWD. El modelo GGUF
(`Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`, ~4.9 GB) **solo existe en el worktree
que posee `main`** (la raíz del repo). Si el stack se levanta desde otro
worktree cuyo `models/` solo tiene `.gitkeep`, `llama-server` entra en
crash-loop. **Siempre levantar el stack desde el worktree raíz (main).**

---

## 4. Verificación obligatoria al finalizar cada sesión

Al terminar una sesión con cambios de código/despliegue, **antes de considerar el trabajo completo**, ejecutar en orden los pasos siguientes y el gate específico del [hito activo](docs/implementacion-agente-vivo/hitos.md), con los oráculos de [verificación](docs/implementacion-agente-vivo/verificacion.md).

**Excepción documental:** si solo se modifican Markdown/instrucciones, comprobar enlaces, coherencia de contratos/IDs y diff; no reconstruir imágenes, redeployar ni ejecutar LLM/DB innecesariamente. No declarar gates operativos aprobados por esa validación.

### 4.1 Reconstruir y redeployar las imágenes

Al finalizar los cambios de código de cada hito, **antes de ejecutar las
pruebas**, actualizar las imágenes que correspondan según los últimos cambios y
volver a desplegarlas en un contenedor. Los cambios en `src/` NO se aplican en
la imagen de tests sin rebuild.

**Imagen del agente (incluye tests):**

```powershell
podman build -t localhost/argentgob-mvp-agent:latest -f containers/agent.Containerfile .
```

**Imagen del migrator** (si se agregó/modificó una migración):

```powershell
podman compose build migrator
```

**Imagen del dashboard** (si se modificó el dashboard):

```powershell
podman compose build dashboard
```

**Redeploy del stack** (recrear los contenedores con las imágenes nuevas):

```powershell
podman compose up -d --force-recreate
```

> **Nota:** el E2E con `-v "${PWD}:/app"` usa el código local directamente, pero
> la suite de tests y los servicios del stack usan la imagen. Por eso el rebuild
> es obligatorio antes de validar.

### 4.2 Suite de tests unitarios (completa)

```powershell
$src = (Get-Location).Path
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/ -q -p no:cacheprovider
```

> **Siempre** añadir `-p no:cacheprovider`. Referencia histórica: **334 passed + 9 skipped**; registrar resultado real, no exigir ese número tras agregar tests. Los skips de DB/LLM no satisfacen un gate que requiere integración real. `pytest tests/` incluye unitarias e integración.

### 4.3 Tests de integración

```powershell
$src = (Get-Location).Path
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/integration -q -p no:cacheprovider
```

### 4.4 Validación del corpus (cuando exista)

El corpus ya existe. En cierres de implementación ejecutar su validación con `python scripts/validate_corpus.py` dentro del contenedor, además de los tests aplicables (ver [comandos](docs/implementacion-agente-vivo/verificacion.md)). Cambios de patrones PII pueden afectar features replicadas: preservar schema/manifest y evaluación congelada o versionar explícitamente el cambio.

### 4.5 E2E del caso (según el hito)

- **Cadena de auditoría (H7):**
  ```powershell
  podman run --rm --network argentgob-mvp_default `
    -e "DATABASE_URL=******postgres:5432/argentgob" `
    -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
    python -m argentgob.audit.cli
  ```
  Exit 0 = `OK: la cadena de auditoría es íntegra.`
- **Consola H8:**
  ```powershell
  podman run --rm --network argentgob-mvp_default `
    -e "DATABASE_URL=******postgres:5432/argentgob" `
    -e "ENVIRONMENT=LOCAL" `
    -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
    python scripts/e2e_h8_console.py
  ```
  Resultado esperado: `===== E2E H8: 23 passed, 0 failed =====`.
- **Capa predictiva (Fase 4):**
  ```powershell
  podman run --rm --network argentgob-mvp_default `
    -e "DATABASE_URL=******postgres:5432/argentgob" `
    -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
    python scripts/e2e_fase4_predictive.py
  ```
  Resultado esperado: `===== E2E FASE 4: 6 passed, 0 failed =====`.
- **Evidencia académica (Fase 5):**
  ```powershell
  podman run --rm --network argentgob-mvp_default `
    -e "DATABASE_URL=******postgres:5432/argentgob" `
    -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
    python scripts/e2e_fase5_evidence.py
  ```
  Resultado esperado: `===== E2E FASE 5: 19 passed, 0 failed =====`.
  - **Demo end-to-end (5 escenarios):**
    ```powershell
    podman run --rm `
      -e "DATABASE_URL=******postgres:5432/argentgob" `
      -e "ENVIRONMENT=LOCAL" -e "LOG_LEVEL=INFO" `
      -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
      python scripts/demo_end_to_end.py [--real]
    ```
    Sin `--real` corre 100% en memoria (sin red ni LLM); con `--real` persiste en
    PostgreSQL. Resultado esperado: 5 escenarios con outcomes `OK`, `BLOCK`
    (`PREDICTED_HIGH_RISK`, `INJECTION_PATTERN_DETECTED`,
    `SENSITIVITY_EXCEEDED`) y `HITL COMPLETO` (approved → resume → ejecución).
  - **Stack (health checks):** `./scripts/verify_stack.sh` (llama-server,
    dashboard, postgres).
- **Agente E2E (análisis de empresa):**
  ```powershell
  podman run --rm --network argentgob-mvp_default `
    -v "${src}:/app:Z" -w /app `
    -e OPENAI_API_BASE=http://llama-server:8080/v1 `
    -e OPENAI_API_KEY=local-no-key-required `
    -e OPENAI_MODEL_NAME=llama-3.1-8b-instruct `
    -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO `
    -e DATABASE_URL=******postgres:5432/argentgob `
    argentgob-mvp-agent:latest python -m argentgob.agent.main AAPL --profile analyst
  ```

### 4.6 Migraciones (si se agregó una)

```powershell
podman run --rm --network argentgob-mvp_default `
  -e "DATABASE_URL=******postgres:5432/argentgob" `
  -v "${PWD}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest `
  python -m alembic upgrade head
```

Verificar cabecera con `python -m alembic current` en el entorno configurado. Último head documentado: `007`; confirmar aplicado y registrar evidencia, no asumirlo por el contenido del repositorio.

---

## 5. Ciclo de vida del código, repo y branches

### 5.1 Branches

- **Al iniciar cada sesión de desarrollo**, crear una rama de trabajo nueva para
  ese hito, con prefijo `agents/`, p. ej. `agents/h12-corpus-predictivo`. Si la
  rama del hito ya existe, usar `git switch` a ella; si no, crearla desde
  `main` (o desde la cabecera verificada).
- El trabajo de la sesión se hace **siempre** en esa rama de trabajo; nunca
  commitear directo sobre `main`.
- Identificar el worktree que posee `main` con `git worktree list`; los nombres históricos de carpetas no son rutas actuales garantizadas. El GGUF está en el checkout raíz de este equipo. No cambiar `main` desde otro worktree ni copiar el modelo innecesariamente. El merge a `main` se hace SOLO al cierre de implementación, después de validar (sección 4):
  ```powershell
  git -C "<worktree-raiz>" merge --ff-only <branch>
  ```
- **Al finalizar la sesión**, una vez validados todos los cambios (tests + E2E),
  ejecutar en orden: (1) `git push -u origin <branch>` para publicar la rama de
  trabajo y (2) el merge `--ff-only` a `main` desde el worktree raíz. Solo
  después de esos dos pasos se considera cerrado el hito.

### 5.2 Commits

- Prefijo convencional: `feat:`, `fix:`, `docs:`, `test:`.
- Añadir el trailer:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```

### 5.3 Regla de release

**No se pushea ni mergea** hasta que: (1) se reconstruyan y redeployen las
imágenes afectadas (sección 4.1), y (2) pasen la suite unitaria y el test
integral (E2E del caso). Ver sección 4.

### 5.4 Config SSH (necesaria para push/fetch)

```bash
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe'
```

**Usar forward slashes.** Sin esto, `git push`/`git fetch` fallan.

### 5.5 Repositorio remoto

`git@github.com:Fran-Benko/abacus-tesis-mvp.git` (remote `origin`).

---

## 6. Trampas recurrentes (quirks)

- **PowerShell `NativeCommandError`**: podman/git escriben en stderr y PS
  reporta "exit 1" aparente aunque el comando tenga éxito. Redirigir a archivo
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
  imagen de tests sin rebuild; **siempre reconstruir la imagen del agente
  (sección 4.1) antes de correr las pruebas**. El E2E con `-v "${PWD}:/app"` usa
  el código local directamente.
- **Quirk de `dict_row`**: `get_connection()` (psycopg3) devuelve filas tuple
  por defecto. Los servicios H8 usan `conn.cursor(row_factory=dict_row)`.
- **`__pycache__`** son dirs gitignoreados (untracked).
- **`.env` y `.secrets/`** NO se comitean (contienen secretos). Nunca exponer
  valores reales de `DATABASE_URL` ni `POSTGRES_PASSWORD`.

---

## 7. Convenciones de código

- Python 3.12, `src/` layout, `pydantic` para config y schemas.
- Ruff: `line-length = 100`, `target-version = py312`.
- Invariantes clave del prototipo (no romper):
  - **INV-01/02** — la herramienta nunca se ejecuta antes de una decisión
    válida; un `BLOCK` aborta la tentativa.
  - **INV-04/05** — el payload crudo nunca se reutiliza ni se persiste; solo
    telemetría sanitizada y digest SHA-256.
  - **INV-11** — ante duda, la política falla cerrada (`BLOCK`).
  - **INV-16** — el detector produce metadata/finding sin valor y no muta
    `execution_arguments`.
- Precedencia de decisión: `BLOCK > HITL > PASS`.

## 8. Reglas específicas de la ampliación AV

- Leer [contratos](docs/implementacion-agente-vivo/contratos.md) y el [gate activo](docs/implementacion-agente-vivo/hitos.md) antes de editar. Actualizar pruebas y documentación con cada unidad, no al final de todo el plan.
- Sanitización de telemetría no equivale a masking operativo ni a minimización del primer prompt/resultados. `USE_TRANSFORMED_ARGUMENTS` actualmente normaliza espacios; no asumir redacción de PII implementada.
- IPI se controla en contenido recuperado antes de entregarlo al LLM. Lectura PASS y liberación BLOCK son hechos distintos, ambos con evidencia coherente.
- Nunca mostrar stdout/verbose/prompts/razonamiento como timeline; usar eventos explícitos, acotados y por allowlist.
- Reutilizar H6/H8 y demostrar revalidación real antes del resume; aprobación no evita guardrails. No repetir `crew.kickoff()` para simular continuidad ni reconstruir hold desde logs.
- Nuevos dominios: datos/organismos simulados y predictor no validado, sin conectores institucionales reales ni efectos externos. No añadir brokers/colas distribuidas ni cinco frameworks de dominio.
- Las demos D-01…D-12 exigen llama.cpp real y sus oráculos; los stubs y tests de tools CrewAI no reemplazan esa evidencia. Selectores/runners futuros se crean en su hito, no se consideran disponibles hoy.
- Cerrar solo con evidencia de tests/PG/LLM requeridos no skipped, imágenes/árbol identificado y [checklist](docs/implementacion-agente-vivo/verificacion.md). Si falta infraestructura, informar BLOCKED, no PASS.
- No ejecutar pruebas destructivas contra el stack compartido; usar DB/copias/proyecto aislados. Nunca `down -v` sobre datos vigentes ni imprimir secretos.
