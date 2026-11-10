# Sesión 0027 — AV-02: Compose e inicialización reproducibles

**Fecha:** 2026-11-10. **Hito:** AV-02. **Gate:** G-AV-02 → **PASS**.
**Rama:** `agents/av-02-compose-inicializacion`. **Base:** `8780935` (AV-01).

## 1. Alcance implementado

| Unidad del hito | Implementación |
|---|---|
| Agent como API persistente sin stdin/tty; healthchecks y readiness coherentes; dashboard independiente de LLM | `compose.yaml` (`agent` con `python -m argentgob.live.server`, sin `ports`, `stdin_open` ni `tty`; healthcheck `/health`), `src/argentgob/live/readiness.py` (`StackReadiness`), `src/argentgob/live/api.py` (`/ready` con `checks` de esquema/catálogo y 503 en `POST /runs`) |
| Inicializador Alembic + seed validado con fallo propagado; revisión de pruning y versiones | `src/argentgob/db/init.py` (`python -m argentgob.db.init`), `src/argentgob/module_c/policy_seeder.py` (seed canónico reutilizable), `scripts/seed_policies.py` (wrapper de compatibilidad) |
| Secretos/variables por consumidor; red/puertos mínimos; rutas GGUF/predictor/fixtures parametrizadas y RO; pin de llama.cpp | `src/argentgob/db/dsn.py` (`build_dsn`, `resolve_database_url`, `read_secret_file`), `compose.yaml` (secretos por servicio, `127.0.0.1` para 8080/8501, imagen llama.cpp por digest, `GGUF_DIR`/`GGUF_FILE`), `scripts/init_secrets.py` |
| Compatibilidad con proveedor Compose real; preflight sin sleeps ciegos | `scripts/preflight_stack.py`, `podman compose config` (v2.39.2) y `service_completed_successfully` |
| Arranque aislado desde DB vacía, upgrade y segundo arranque; smoke de imagen sin montar fuentes | `scripts/verify_stack_av02.py` (24 checks reales), `containers/agent.Containerfile` y `containers/dashboard.Containerfile` con `COPY data/` |

Además: migraciones `008` (roles de runtime con mínimo privilegio), `009` (CONNECT/TEMPORARY a nivel de base) y `010` (lectura de `alembic_version`), y `RISK_MODEL_DIR`/`RISK_CORPUS_PATH` respetados por `RiskPredictor` y `EvidenceService`.

## 2. Decisiones de contrato

1. **Un único inicializador obligatorio.** `migrator` ejecuta `python -m argentgob.db.init`: upgrade, provisión de roles, seed y verificación de postcondiciones. Cualquier fallo propaga exit != 0 y `agent`/`dashboard` no arrancan (`service_completed_successfully`). Esto cierra la brecha 1 de AV-00 sin depender de un paso manual.
2. **El archivo de secreto es la fuente canónica del DSN.** `resolve_database_url()` prioriza `*_PASSWORD_FILE` sobre una URL heredada de `.env`, de modo que el despliegue no depende de credenciales embebidas. El escape usa `quote(..., safe="")`, probado con `@:/#%?&=`.
3. **Roles separados por consumidor.** `argentgob_runtime` escribe evidencia y ejecuta tools, sin DDL ni superusuario; `argentgob_dashboard` lee vistas sanitizadas y resuelve approvals, sin escribir decisiones. `argentgob_app` queda como rol administrativo/migración.
4. **`/ready` mide el stack, no solo el proceso.** `ready` exige `db_reachable`, `schema_en_head` y `catalogo_sembrado`; `capabilities` informa LLM, predictor y cupo. Sin LLM, `/health` y `/ready` responden y el dashboard sigue operativo, pero `POST /runs` devuelve 503.
5. **Sin publicación de puertos internos.** `agent` no declara `ports`; llama.cpp y el dashboard se publican solo en `127.0.0.1`. El token de servicio se monta como archivo (`AGENT_API_SERVICE_TOKEN_FILE`).
6. **Artefactos dentro de la imagen.** `data/` se copia en ambas imágenes y `RISK_MODEL_DIR` apunta a `/app/data/models/risk_model_v1`; un contenedor sin bind mount mantiene `RiskPredictor.available=True` (T-A07).

## 3. Evidencia ejecutada

Árbol verificado: rama `agents/av-02-compose-inicializacion` sobre `8780935`, con cambios sin commitear al momento de la medición. Imágenes `localhost/argentgob-mvp-agent:latest`, `argentgob-mvp-dashboard` y `argentgob-mvp-migrator` reconstruidas y redeployadas.

| Verificación | Comando | Resultado |
|---|---|---|
| Gate de topología | `python scripts/verify_stack_av02.py` | **24 passed, 0 failed** |
| Gate AV-02 (sin LLM) | `python scripts/verify_agent_live.py --milestone AV-02` | `collected: 96`, `96 passed`, `result: PASS` |
| Gate AV-02 (con LLM real) | `python scripts/verify_agent_live.py --milestone AV-02 --with-llm` | `96 passed` + `1 passed` (LLM), `result: PASS` |
| Suite completa | `pytest tests/ -q -p no:cacheprovider` | **428 passed, 10 skipped** |
| PG real | `pytest tests/integration/test_postgres_real.py` con `TEST_DATABASE_URL` | `7 passed` |
| E2E H8 (DB aislada) | `python scripts/e2e_h8_console.py` | `23 passed, 0 failed` |
| Fase 4 / Fase 5 | `e2e_fase4_predictive.py` / `e2e_fase5_evidence.py` | `6 passed` / `19 passed` |
| Contrato HTTP desplegado | `python scripts/api_check_agent_live.py --base http://agent:8090 --service-token-file ...` | `16 passed, 0 failed` |
| Compose real | `podman compose config` / `podman compose up -d` | exit 0; `migrator` exit 0; `agent` y `dashboard` healthy |
| Readiness desplegada | `GET /ready` desde el contenedor | `ready=true`, `revision=010`, `policies=8` |
| Degradación sin LLM | `podman stop llama-server` + `GET /ready` + `POST /runs` | `llm_reachable=false`; `POST /runs` → 503; dashboard operativo |
| Imagen sin bind mount | `RiskPredictor()` dentro de la imagen | `available=True`, prompts/policies/migrations/corpus presentes |
| Corpus | `python scripts/validate_corpus.py` | `RESULTADO: OK` |
| Demo determinista | `python scripts/demo_end_to_end.py` | 5 escenarios OK/BLOCK/HITL |
| CLI financiera | `python -m argentgob.agent.main AAPL --profile analyst` | `status=SUCCESS` |
| Cadena de auditoría | `python -m argentgob.audit.cli` | `OK: la cadena de auditoría es íntegra.` |

## 4. Brechas de AV-00 cerradas

| Brecha | Evidencia de cierre |
|---|---|
| Inicializador sin seed | `python -m argentgob.db.init` siembra 8 políticas y verifica `governance_policies > 0`; T-A01/T-A04 en el gate |
| Identidad HITL sin configurar | `compose.yaml` declara `IDENTITY_FIXTURE_ROLES` y `AUTHORIZED_AUDITOR_ROLES`; sonda en el contenedor: `has_auditor=True`, `assurance=DEVELOPMENT` |
| Rol de runtime superusuario | Migraciones `008`–`010`; T-A08 verifica `rolsuper=false`, DDL bloqueado y escritura de evidencia permitida |
| Predictor ausente en la imagen | `COPY data/` + `RISK_MODEL_DIR`; T-A07 verifica `available=True` sin bind mount |

## 5. Limitaciones registradas

- El gate de topología cambia temporalmente las contraseñas de los roles de clúster (son globales, no por base) y las restaura al finalizar. Si el proceso se interrumpe, re-ejecutar `python -m argentgob.db.init`.
- `POST /runs/{id}/resume`, la UI financiera en vivo y los cinco dominios públicos siguen en AV-03/AV-05.
- El estado de runs y eventos vive en memoria del proceso padre: un reinicio pierde runs, como declara el contrato.
- `max_json_depth`, `max_json_nodes`, `privacy_scan_timeout_ms` y `agent_profile` siguen sin aplicarse fuera de `config.py`; no se documentan como activos.
- La imagen del dashboard no incluye `migrations/` (no las necesita) y el agente no publica el puerto 8090 al host por diseño.
- Los 10 skips de la suite son los mismos de AV-01: 7 de PG real sin `TEST_DATABASE_URL`, 2 de AppTest sin `DATABASE_URL` y 1 de LLM sin `DATABASE_URL`. La evidencia PG/LLM se tomó con variables configuradas y no quedó skipped.

## 6. Próximo hito

**AV-03 — UI financiera en vivo**, con entrada G-AV-02 PASS y la API/eventos disponibles por red interna (`AGENT_API_BASE=http://agent:8090`).
