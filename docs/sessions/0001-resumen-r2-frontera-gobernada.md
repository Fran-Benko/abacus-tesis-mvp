# Resumen de sesión — Preparación para R2 (Frontera gobernada)

Fecha: 2026-09-17
Rama: `agents/r2-continuacion-plan` (creada desde `main` en `11c8a7e`)
Plan: `mvp-plan-argentgob-v0.3-ampliado.md` (fuente normativa, NO está en el repo; está en `~/Downloads/`)

## Objetivo de la próxima sesión

Implementar **R2 — Frontera gobernada** del track R del plan. Es la siguiente
unidad lógica y la que más falta del track R.

## Estado del repositorio

- `main` → `11c8a7e` — sincronizada con `origin/main` (R1 + observaciones mergeados y pusheados).
- `agents/r2-continuacion-plan` → `11c8a7e` — **branch actual**, creada desde main, working tree limpio.
- `agents/plan-implementacion-avancemos` → `11c8a7e` — branch anterior (ya mergeada, aún existe).
- `feat/r0-reconciliation-baseline` → `78deae4` — branch de referencia (worktree del repo principal).

### Configuración SSH resuelta (importante)

Git usaba el SSH de Git for Windows (OpenSSH 10.2) que fallaba con
"Permission denied (publickey)". El SSH de Windows (`C:\Windows\System32\OpenSSH\ssh.exe`,
OpenSSH 9.5) sí autentica. Se configuró en el repo:

```bash
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe'
```

**Usar forward slashes** (los backslashes se rompen al pasarlos al shell).
Sin esto, `git push`/`git fetch` fallan.

## Baseline de tests

- **54 tests pasan** (17.58s) vía:
  `podman run --rm -v "${PWD}:/app" -w /app argentgob-mvp-agent:latest python -m pytest tests/ -q`
- **Ruff NO está instalado** en la imagen del agente.

## Evaluación del Track R

| R | Estado | Falta |
|---|--------|-------|
| R1 | ✅ Completo | — |
| R2 | ⚠️ Parcial | Selección de argumentos, allowlist de hosts, decisión vencida, redirect prohibido a nivel PEP |
| R3 | ✅ Completo | — |
| R4 | ⚠️ Parcial | Property tests del backoff (Hypothesis), límite con retries ocultos |
| R5 | ⚠️ Parcial | No-leak automatizado, logs JSON de intentos |

## R2 — Frontera gobernada (lo que hay que implementar)

### Requisito del plan (textual)

> R2 — Frontera gobernada: integrar selección de argumentos y comprobación de
> cada destino usando PEP existente. Probar ausencia de decisión, BLOCK, HITL,
> identidad incompleta, digest incorrecto y redirect prohibido: cero solicitudes
> no autorizadas. Probar decisión vencida durante backoff: no nuevo intento.

### Qué ya existe (reutilizar)

- **PEP** (`GovernanceMiddleware.pre_hook`) en `src/argentgob/module_a/governance.py`:
  construye envelope, valida payload limit, sanitiza, evalúa ABAC, corre guardrails,
  registra decisión. BLOCK lanza `HookAborted` antes del side effect.
- **Digest canónico** en `src/argentgob/core/envelope.py` (`_canonical_digest`):
  SHA-256 con `sha256:` prefix, claves ordenadas, separadores estables, UTF-8.
- **Envelope** separa `execution_arguments` (solo memoria) de `telemetry_arguments`
  (sanitizados para logs/DB).
- **GovernedTool._run** en `src/argentgob/module_a/governed_tool.py`: pre_hook →
  `_execute` → post_hook. BLOCK aborta antes de ejecutar.
- **ABACEvaluator** en `src/argentgob/module_c/abac_evaluator.py`: evalúa por
  `tool_name` contra `allowed_tools` del perfil (con fallback in-memory si DB cae).
- **Tests PEP** en `tests/unit/test_pep.py` y **tests integración** en
  `tests/integration/test_governance_flow.py` (usan `SpyTool` + mocks de audit).

### Qué falta (implementar)

1. **Selección de argumentos** — El PEP pasa `execution_arguments` intactos.
   Falta la obligación `USE_ORIGINAL_ARGUMENTS` / `USE_TRANSFORMED_ARGUMENTS`
   (reglas comunes: exactamente una; ninguna o ambas → `POLICY_CONFLICT` y cero
   ejecución). La tool debe recibir el original intacto o el objeto transformado
   validado, nunca preview.

2. **Comprobación de destino por host (allowlist)** — El `resource` es
   `news_providers` genérico; no valida el host exacto. El plan exige autorizar
   hosts exactos (GDELT: `api.gdeltproject.org`, DDG: `duckduckgo.com`) y
   verificar redirects antes de cada salida. Deshabilitar redirects si no pueden
   validarse. No aceptar sufijo amplio ni descubrir hosts automáticamente.

3. **Decisión vencida durante backoff** — `PolicyDecision` no tiene `expires_at`.
   El plan exige: si la decisión vence o deja de aplicar durante el backoff,
   abortar y requerir nueva ejecución gobernada (no nuevo intento).

4. **Redirect prohibido a nivel PEP** — Probar que un redirect a host no
   autorizado bloquea (cero solicitudes no autorizadas).

5. **HITL** — Probar que HITL no llama a la tool (aunque la implementación
   durable de HITL es H6, R2 pide probar el comportamiento de no-ejecución).

### Casos de prueba que exige R2 (cero solicitudes no autorizadas)

- Ausencia de decisión → no ejecuta.
- BLOCK → no ejecuta (ya cubierto, extender).
- HITL → no ejecuta.
- Identidad incompleta → no ejecuta.
- Digest incorrecto → no ejecuta.
- Redirect prohibido → no ejecuta.
- Decisión vencida durante backoff → no nuevo intento.

## Archivos clave

- `src/argentgob/module_a/governance.py` — PEP (pre_hook/post_hook).
- `src/argentgob/module_a/governed_tool.py` — wrapper base gobernado.
- `src/argentgob/core/envelope.py` — envelope + digest canónico.
- `src/argentgob/core/decision.py` — PolicyDecision (falta `expires_at`).
- `src/argentgob/module_c/abac_evaluator.py` — evaluación ABAC.
- `src/argentgob/tools/news_tool.py` — tool news (GDELT→DDG).
- `src/argentgob/tools/news_providers.py` — proveedores + hosts autorizados.
- `tests/unit/test_pep.py` — tests PEP existentes.
- `tests/integration/test_governance_flow.py` — tests integración.
- `docs/decisions/0001-r1-contrato-noticias.md` — ADR R1 (hosts como capa interna).
- `docs/observations/0001-observaciones-prueba-e2e.md` — observaciones E2E.

## Notas / decisiones previas relevantes

- **Hosts autorizados como capa interna de la tool** (ADR-0001): no como campo
  de policy. R2 debe integrar la comprobación de destino usando el PEP existente.
- **Clasificación `EXTERNAL_SEND`** con excepción acotada de retries (solo
  noticias, máx 4 intentos/proveedor). No reclasificar como READ.
- **Normalización mínima**: solo `strip()` de espacios en `query`.
- **Contradicción del master-plan**: la búsqueda está clasificada `EXTERNAL_SEND`
  (norma prohíbe retries) pero el usuario pide retries de noticias. Resuelto en
  ADR-0001 con excepción acotada. No resolver silenciosamente.

## Comandos útiles

```bash
# Tests
podman run --rm -v "${PWD}:/app" -w /app argentgob-mvp-agent:latest python -m pytest tests/ -q

# Rebuild imagen del agente (tras cambios en src/)
podman build -t argentgob-mvp-agent:latest -f containers/agent.Containerfile .

# Push (requiere core.sshCommand configurado)
git push origin main
```