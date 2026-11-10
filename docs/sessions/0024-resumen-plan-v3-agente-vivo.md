# Sesión 0024 — Plan v3 e instrucciones para agente en vivo

**Fecha:** 2026-11-10. **Rama:** `agents/plan-v3-hitos-gobernanza`.
**Tipo:** documentación e instrucciones; sin implementación ni cambios de infraestructura.

## Entrega

Se convirtió la revisión v2 en [documentación rectora versionable](../implementacion-agente-vivo/README.md) con seis documentos:

- [Hitos](../implementacion-agente-vivo/hitos.md): AV-00…AV-06, DAG, entrada, unidades, tests, gates y entregables.
- [Contratos](../implementacion-agente-vivo/contratos.md): flujos, inputs/outputs, API, eventos, identidad, estados, HITL e invariantes.
- [Escenarios](../implementacion-agente-vivo/escenarios.md): cinco contextos, tools/perfiles/YAML mínimos, guardrails y D-01…D-12 con LLM local real.
- [Despliegue](../implementacion-agente-vivo/despliegue.md): servicios, inicialización, configuración/límites, secretos, artefactos y degradación.
- [Verificación](../implementacion-agente-vivo/verificacion.md): 48 grupos de casos T-*, oráculos, runners disponibles/propuestos y evidencia de cierre.
- Índice con baseline histórico, autoridad documental, trazabilidad de requisitos y estado pendiente de todos los AV.

Se actualizó [AGENTS.md](../../AGENTS.md) para iniciar por la nueva documentación y corregir referencias desactualizadas: última suite documentada 334/9, migraciones presentes hasta 007, dashboard v4, E2E llama.cpp registrado en 0023 y distinción entre propuestas y capacidades existentes. Se añadió excepción documental y reglas AV sin modificar código del producto. [Consideraciones](../PROJECT_CONSIDERATIONS.md) enlaza la nueva hoja de ruta.

## Decisiones preservadas

- Sin Redis/brokers/colas distribuidas, plugins o conectores institucionales reales.
- PII protegida en entrada, resultados y presentación; no solo telemetría.
- IPI se controla antes de liberar documentos al LLM; lectura y liberación auditadas por separado.
- HITL ejecuta una acción retenida como máximo una vez; no replay automático del Crew.
- Predictor financiero no se presenta como validado para nuevos dominios.
- Demos con inferencia real separadas de dobles de prueba y de `--real` de la demo histórica, que significa PG.

## Validación documental

Se comprobaron enlaces relativos, UTF-8 sin BOM, fences balanceados, cobertura de IDs T/D y campos obligatorios de los siete hitos. `git diff --check` sin errores. Cambios limitados a Markdown/instrucciones; no se modificaron `src`, tests, dependencias ni compose.

No se ejecutaron suite de aplicación, LLM, PG, migraciones ni redeploy. **Ningún gate AV queda aprobado** por esta entrega. Las cifras 334 passed + 9 skipped y revisión aplicada 007 son referencias históricas: deben reconfirmarse en AV-00.

## Continuación

Comenzar por [AV-00](../implementacion-agente-vivo/hitos.md): baseline operativo, contratos mapeados a rutas reales y decisiones mínimas sobre persistencia de control de contenido, identidad y resume. Runners/tests futuros deben crearse en el hito correspondiente y su selección debe comprobarse no vacía antes de usarse como gate.
