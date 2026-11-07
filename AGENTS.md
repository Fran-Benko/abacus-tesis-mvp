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
| [`docs/plan-continuacion.md`](docs/plan-continuacion.md) | **Hoja de ruta actual**: fases para incorporar la capa predictiva (H12–H16) sobre la base H1–H11 |
| [`docs/sessions/`](docs/sessions/) | Resúmenes de sesión por hito (qué se implementó, tests, E2E, bugs) |
| [`docs/modulo2-entrega.md`](docs/modulo2-entrega.md) | Entrega del Módulo 2 (estado documentado del prototipo) |

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
- **Suite de pruebas:** **286 passed + 6 skipped** (unit + integración; los 6 skipped son de PostgreSQL real sin `TEST_DATABASE_URL`), los cinco sesgos de la Fase 6 quedaron cerrados (`docs/analytics/0004`) y el monitoreo de deriva del modelo quedó implementado (`docs/sessions/0018`).
- **Migraciones aplicadas:** 001 → 002 → 003 → 004 → 005 → **006** (head).
- **Dashboard:** Streamlit con KPIs, decisiones, eventos, distribución por
  herramienta, sección H8 (consola sanitizada + resolución autorizada) y
  sección Fase 5 (evidencia predictiva).
- **E2E H8:** `scripts/e2e_h8_console.py` → 23/23 checks OK.
- **E2E Fase 4:** `scripts/e2e_fase4_predictive.py` → 6/6 checks OK.
- **E2E Fase 5:** `scripts/e2e_fase5_evidence.py` → 19/19 checks OK.
- **Rama de trabajo:** `main` (worktree raíz, posee el modelo GGUF).

### Próximo hito

La hoja de ruta de [`docs/plan-continuacion.md`](docs/plan-continuacion.md)
(Fases 0–6) está **completa**. El trabajo pendiente es de prioridad alta y no
bloqueante: completar criterios de aceptación de H1–H4 y H8–H11, verificar
HITL end-to-end con el modelo activo, detallar relaciones y cardinalidades en
la entrega del Módulo 2, y descomponer los sprints en tareas operativas.

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

Al terminar cualquier sesión de desarrollo, **antes de considerar el trabajo
completo**, ejecutar en orden:

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

> **Siempre** añadir `-p no:cacheprovider`. Resultado esperado: **286 passed + 6 skipped** (271 + 15 nuevos de deriva; PG real se salta sin `TEST_DATABASE_URL`).

### 4.3 Tests de integración

```powershell
$src = (Get-Location).Path
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/integration -q -p no:cacheprovider
```

### 4.4 Validación del corpus (cuando exista)

Cuando la capa predictiva (H12–H16) tenga un corpus analítico construido, se
debe **agregar la validación del corpus** a la verificación de cierre de sesión:
correr los tests sobre el corpus (unit + integración + validación de corpus)
para confirmar el correcto funcionamiento. Hasta que el corpus exista, este
paso no aplica.

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

Verificar cabecera con `python -m alembic current` (estado actual: `006 (head)`).

---

## 5. Ciclo de vida del código, repo y branches

### 5.1 Branches

- **Al iniciar cada sesión de desarrollo**, crear una rama de trabajo nueva para
  ese hito, con prefijo `agents/`, p. ej. `agents/h12-corpus-predictivo`. Si la
  rama del hito ya existe, usar `git switch` a ella; si no, crearla desde
  `main` (o desde la cabecera verificada).
- El trabajo de la sesión se hace **siempre** en esa rama de trabajo; nunca
  commitear directo sobre `main`.
- El worktree que mantiene el checkout de `main` es la **raíz del repo**
  (`plan-implementacion-avancemos`). No se puede `git checkout main` desde otro
  worktree. El merge a `main` se hace SOLO al cierre de la sesión, después de
  validar (sección 4):
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