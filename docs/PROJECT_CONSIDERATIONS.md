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
baseline 71 → R4 +7 = 78 → R5 +8 +1 logger = 87 → H5 +15 = 102 → H6 +24 =
**126 passed**.

### 3.2 Tests de integración

El runner es el mismo pero apuntando a `tests/integration`:

```powershell
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/integration -q -p no:cacheprovider
```

Actualmente: **6 passed**.

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

Estado actual: migraciones aplicadas hasta `002 (head)`.

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

---

## 4. Convención de branches / git

- El worktree que mantiene el **checkout de `main`** es `plan-implementacion-avancemos`.
  No se puede `git checkout main` desde otro worktree. El merge a `main` se hace:
  ```powershell
  git -C "<plan-implementacion-avancemos>" merge --ff-only <branch>
  ```
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
  `DeterministicEvaluator`/`PolicyStore` + servcios HITL, 24 tests propios →
  baseline 126 passed). Merge a `main` pendiente.
- Próximo hito según plan: **H7 — Auditoría detectable ante manipulación** (ver
  resúmenes de sesión para análisis detallado).