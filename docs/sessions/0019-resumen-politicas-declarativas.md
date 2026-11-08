# Resumen de sesión 0019 — Políticas declarativas (YAML) como fuente única

**Hito:** Prioridad media del `docs/plan-continuacion.md` §7 — *"Refinar políticas declarativas"*.
**Rama:** `agents/hito10-politicas-declarativas` · merge `--ff-only` a `main`.
**Fecha:** 2026-11-07/08

---

## Problema resuelto

Los archivos `policies/*.yaml` existían y el README los describía como *"políticas
ABAC declarativas"*, pero **ningún componente las leía**: `scripts/seed_policies.py`
generaba las políticas desde los perfiles hardcodeados (`module_c.profiles`) y el
runner nunca tocaba los YAML. La fuente real de verdad era código, no
configuración declarativa.

Este hito cierra esa brecha: los YAML pasan a ser la **fuente canónica** de
`governance_policies`, con validación estricta y ejercicio de los campos
declarativos que el modelo `GovernancePolicy` ya soportaba pero no se usaban
(`sensitivity_limit`, `resource`, `valid_from/until`, `transform_spec`).

---

## Entregables (implementación)

### `src/argentgob/module_c/yaml_policies.py` (nuevo)
- `parse_policy_file`: parsea `policies/*.yaml` con esquema estricto y errores
  descriptivos (`PolicyYamlError` con archivo/`policy_id`/campo).
- `policy_from_mapping`: normaliza el vocabulario declarativo al modelo interno
  (`ALLOW/DENY` → `PASS/BLOCK`, obligations con alias, ISO-8601 con zona horaria).
- Expansión del comodín `*` (tool/operation_class) a las herramientas concretas
  del perfil, generando IDs únicos (`POL-admin-001-NEWS`, etc.) para no
  colisionar en la clave compuesta. El evaluador exige matching exacto, nunca
  wildcard libre en runtime.
- `load_all`: carga todos los YAML; si no hay archivos, degrada a los perfiles
  in-memory (`policies_from_profiles`) para no dejar el enforcement sin fuentes.
- `to_seed_rows`: serializa al contrato del INSERT del seed (JSON de
  obligations/transform_spec, `valid_from` con default `now()`).

### `scripts/seed_policies.py` (refactor)
- `seed()` lee `load_all()` (YAML) y hace **UPSERT** sobre
  `(policy_id, policy_version)` con todos los campos declarativos.
- **Pruning**: elimina políticas del mismo environment que ya no están
  declaradas en los YAML (fuente única efectiva).
- `connection_factory` inyectable para tests contra `_mem_db`.
- `setup_logging()` movido a `main()`: importar el módulo ya no reconfigura
  structlog globalmente (esto rompía `capture_logs` en la suite).

### `src/argentgob/module_c/deterministic_evaluator.py`
- Ejerce `sensitivity_limit`: si la predicción del modelo supera el límite
  declarado, la decisión es BLOCK con `ReasonCode.SENSITIVITY_EXCEEDED`
  (reason code que ya existía pero nunca se emitía).
- Helper `_risk_rank()` para comparar severidades LOW < MEDIUM < HIGH.

### `policies/*.yaml` (actualizados)
- Ahora declaran `obligations` (exactamente una de selección, exigida por el
  evaluador) y `sensitivity_limit` donde aplica:
  - `admin.yaml`: comodín `*` → 3 políticas (news/stock/crypto) con
    `sensitivity_limit: HIGH` y prioridad 10.
  - `analyst.yaml`: `news` con `sensitivity_limit: MEDIUM` (la llamada que
    dispara el chequeo nuevo en producción).

### Tests
- `tests/unit/test_yaml_policies.py` (11): parseo, expansión, errores, fallback,
  serialización.
- `tests/unit/test_policy_sensitivity.py` (5): chequeo de límite, caso sin
  predicción, sin límite, ranking.
- `tests/integration/test_policy_seed_declarativo.py` (3): seed → `_mem_db`,
  relectura vía `PolicyStore._row_to_policy`, idempotencia y pruning.
- `tests/integration/test_postgres_real.py`: `test_seed_declarativo_persiste_en_pg_real`
  (seed real, conteo, grants mínimos de `governance_policies`).

---

## Verificación (gate de release)

| Check | Resultado |
|---|---|
| Suite completa (unit + integración, contenedor) | **305 passed + 7 skipped** |
| PostgreSQL real (`argentgob_test`) | **7 passed** (head 007, seed YAML, grants) |
| Seed contra stack real (`argentgob`) | 8 políticas, pruning de 8 huérfanas legacy |
| E2E Fase 4 | 6/6 |
| E2E Fase 5 | 19/19 |
| E2E H8 | 23/23 |
| Flujo agéntico real (`test_agentic_flows`) | 6/6 |
| Ruff | limpio en todos los archivos nuevos/modificados |

**Suite:** 305 passed (297 previos + 8 nuevos: 11 unit YAML + 5 sensibilidad + 3
seed integración + 1 PG real − 12 ajustados por dedupe/remanente).

---

## Proyección

Quedan de prioridad media:
- Mejorar visualizaciones históricas.
- Detector PII probabilístico (fuera de alcance hasta nuevo aviso).

Los YAML son ahora una fuente única versionable en git: cambiar una política es
editar un `.yaml` y correr `python -m scripts.seed_policies` (UPSERT +
pruning), sin tocar código.