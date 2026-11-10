# Pruebas, oráculos y cierre por hito — v3

**Especificación de pruebas futuras, no reporte de ejecución.** [Hitos](hitos.md), [contratos](contratos.md), [demos](escenarios.md). No dar por existentes los nuevos módulos/runners/markers.

## 1. Niveles y archivos objetivo

| Nivel | Dependencias | Cobertura / rutas objetivo propuestas |
|---|---|---|
| Unitario/contrato | Sin red, reloj falso, contadores y barreras | `tests/unit/test_agent_server.py`, `test_run_supervisor.py`, `test_run_events.py`, `test_public_schemas.py`, `test_public_guardrails.py`, `test_content_boundary.py` |
| Integración determinista | CrewAI/tools reales; proveedor/LLM dobles explícitos | `tests/integration/test_agent_live_flow.py`, `test_public_agentic_flows.py`; reutilizar tests de perfiles/PEP/HITL existentes |
| Integración PG real | DB/roles de prueba, esquema y servicios reales | `tests/integration/test_agent_live_postgres.py`, `test_public_hitl_postgres.py`; extender pruebas H6/H7/H8 relacionadas |
| UI | AppTest y API controlada | `tests/unit/test_agent_live_apptest.py`; navegador para evidencia incremental real |
| LLM local real | llama.cpp + CrewAI + fixtures/PG reales | `tests/integration/test_agent_live_llm.py` y runner futuro `scripts/e2e_agent_live.py` |
| Despliegue/imagen | Podman Compose y secretos de prueba | Gate de arranque inicial/repetido, fallos, smoke sin fuentes montadas |

Un archivo por responsabilidad cuando sea necesario; no crear módulos vacíos por cada ID de esta lista. Los IDs T agrupan escenarios de prueba y pueden parametrizarse. Reutilizar tests existentes en vez de duplicarlos por nomenclatura. Ningún nuevo test dependiente de red debe caer por defecto en la suite sin opt-in.

## 2. Casos de aceptación identificados

### A. Despliegue

| ID | Prueba | Oráculo y evidencia |
|---|---|---|
| T-A01 | Compose con base nueva aislada | Migración en head esperado, catálogo sembrado, inicializador exit 0 y consumidores listos |
| T-A02 | Segundo arranque | Sin duplicados ni pérdida de políticas/auditoría; versiones/digests estables |
| T-A03 | Base existente/upgrade | Datos conservados, roles/grants válidos; ninguna política ajena borrada |
| T-A04 | Semántica de depends_on real | Consumidores no admiten antes de inicializador; falla seed bloquea admisión |
| T-A05 | GGUF/secreto/config inválidos | Preflight/readiness falla seguro sin imprimir valores; no prometer CPU fallback |
| T-A06 | DB/LLM/predictor ausentes | Conducta según tabla de degradación; dashboard consultable sin LLM; cero efectos no auditados |
| T-A07 | Imagen sin bind mount de fuentes | Módulos/prompts/YAML/fixtures/artefactos necesarios presentes y legibles como usuario runtime |
| T-A08 | Red/puertos/roles | Agent/PG no publicados, dashboard loopback, mínimo privilegio y contraseña con caracteres reservados funciona |

### B. API, supervisor y UI

| ID | Prueba | Oráculo y evidencia |
|---|---|---|
| T-B01 | Payload/esquema/límites | 413/422 sin eco raw, cero llamadas LLM/tool; cuerpo limitado antes de parseo caro |
| T-B02 | Idempotencia/carreras/rerun | Mismo actor/clave/input no crea otro proceso; clave conflictiva y cupo activo → 409; AppTest no reenvía POST |
| T-B03 | Identidad/perfil/ámbito | `admin`, perfil ajeno, identidad inválida, guardrail desconocido: no ejecutar; runs de otro actor no visibles |
| T-B04 | Ruta pública de tool | PASS ejecuta con decisión válida; BLOCK/HITL cero executor; no usar `_execute` directo como integración |
| T-B05 | Orden y cursor | Dos tentativas de misma tool tienen IDs distintos, seq monotónica, página sin duplicados y truncado explícito |
| T-B06 | Observación incremental | Proveedor retenido con barrera: evento disponible antes de terminar; publicación ≤1 s, visibilidad ≤3 s en entorno medido; sin stdout/prompts |
| T-B07 | TTL/memoria/IPC | Límites de eventos/runs/resultados/cola efectivos; purga y tombstones acotados; polling cesa al finalizar |
| T-B08 | Timeout/reinicio | Proceso termina antes de liberar cupo, no retry tras posible efecto, hold perdido no recuperable; estado indeterminado si aplica |
| T-B09 | Estados/resultados/UI | PASS+fallo proveedor distinto de BLOCK, informe incompleto PARTIAL, errores seguros, dos sesiones, recarga y API caída sin excepciones |

### P. PII y transformación

| ID | Prueba | Oráculo y evidencia |
|---|---|---|
| T-P01 | Entrada al primer prompt | Input PII fuera de cobertura rechazado; permitidos minimizados; canarios no entran a mensajes LLM |
| T-P02 | Resultado proveedor→LLM | Campos tipados DNI/CUIL/domicilio/ingreso/situación excluidos; listas/números/Unicode cubiertos |
| T-P03 | Detector/transformador | Original intacto; findings sin valor; copia nueva y campos permitidos; digest/tamaño/esquema válidos; fallo transforma → cero efecto |
| T-P04 | Telemetría y persistencia | Canarios ausentes de DB, logs, spans, eventos y errores; no basta que no aparezcan en UI |
| T-P05 | Informe/API/UI | Proyección/sanitización de salida LLM, HTML/ANSI y errores sin raw; sin tokens ni cadena de pensamiento |
| T-P06 | Contrato predictivo/corpus | Features actuales preservadas o cambio versionado explícito; partición congelada intacta; sin asumir patrones replicados sincronizados |

### I. Liberación de contenido e IPI

| ID | Prueba | Oráculo y evidencia |
|---|---|---|
| T-I01 | Texto limpio | Lectura y liberación permitidas; contenido minimizado útil llega al LLM |
| T-I02 | Texto retenido | Lectura PASS diferenciada de liberación BLOCK; evidencia durable; texto retenido ausente del LLM |
| T-I03 | Variantes cubiertas | Idioma/espaciado/normalización, tamaño y timeout; no pasos inducidos; hallazgos sin fragmento raw |
| T-I04 | Referencia benigna | Cita académica sobre inyección no produce bloqueo indiscriminado bajo los casos acordados; documentar falsos positivos residuales |
| T-I05 | Control desconocido/fallo | No ignorar control ni liberar resultado si falla; test de migración si persistencia fue ampliada |

### H. HITL

| ID | Prueba | Oráculo y evidencia |
|---|---|---|
| T-H01 | Pendiente | Aprobación durable y hold acotado; executor de escritura cero |
| T-H02 | Autorización | Viewer/actor ajeno no resuelve ni reanuda; approver autorizado y evidencia H8 sí |
| T-H03 | Resume y carrera | Aprobación válida, dos resumes/replay token → como máximo un executor, capability de un uso, no segundo kickoff |
| T-H04 | Rechazo/expiración | Estado terminal coherente, cero escritura, cupo liberado tras fin confirmado |
| T-H05 | Hold perdido/reinicio | Error seguro sin consumir token indebidamente ni reconstruir argumentos |
| T-H06 | Política/digest/guardrail cambiados | Revalidación efectiva antes del efecto; aprobación no elimina BLOCK; no confiar solo en docstrings |
| T-H07 | Persistencia pos-efecto falla | Estado/diagnóstico indeterminado sin retry automático ni resultado exitoso inventado |

### D/J. Auditoría y normativa

| ID | Prueba | Oráculo y evidencia |
|---|---|---|
| T-D01 | Correlación pública | Run/tentativa/decisión/resultado referenciables; eventos UI coinciden con evidencia persistida |
| T-D02 | Cadena válida | CLI verifica integridad y conserva límites tamper-evident |
| T-D03 | Copia alterada | Verificador detecta alteración en copia aislada; original intacto |
| T-J01 | Consulta cubierta | Citas/IDs existen en evidencia recuperada, versión/jurisdicción/fecha consistentes |
| T-J02 | Sin fuente | Abstención INSUFFICIENT_EVIDENCE, no cita inventada |
| T-J03 | Vigencia desconocida | Advertencia explícita, sin afirmar norma vigente por simple coincidencia de texto |
| T-J04 | Salida inválida del LLM | Validador retiene/corrige mediante flujo acotado explícito, sin publicar objeto inválido ni retry infinito |

### R. Regresión, evidencia y realidad del LLM

| ID | Prueba | Oráculo y evidencia |
|---|---|---|
| T-R01 | Baseline | Registrar suite/commit y skips reales, no imponer cantidad histórica como resultado actual |
| T-R02 | Funciones anteriores | CLI financiera, demo 5/5, dashboard, YAML y H8/Fase4/Fase5 siguen funcionando |
| T-R03 | Predicción/precedencia | LOW/MEDIUM/HIGH controlados, límite MEDIUM vs HIGH, BLOCK > HITL > PASS; no confundir stubs con modelo real |
| T-R04 | Corpus | Validador existente exit 0, schema/manifest y partición congelada coherentes |
| T-R05 | LLM real y D-01…D-12 | Servidor local invocado con modelo declarado; tentativa requerida y oráculos de cada D satisfechos; sin monkeypatch de LLM |
| T-R06 | Evidencia completa | Cada gate vincula requisito→test descubierto/ejecutado→exit code→resultado sanitizado, configuración/imagen y commit correctos |

## 3. Ejecución y comandos

### Disponibles hoy

Toda prueba de aplicación dentro de Podman. Después de cambios de código, rebuild/redeploy de imágenes afectadas **antes de validar**. Ajustar el despliegue a AV-02 cuando exista; no ejecutar ahora un servidor/ruta propuestos.

```powershell
$src = (Get-Location).Path
podman build -t localhost/argentgob-mvp-agent:latest -f containers\agent.Containerfile .
if ($LASTEXITCODE -ne 0) { throw 'Fallo de build' }
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/ -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw 'Fallo de suite' }
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest pytest tests/integration -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw 'Fallo de integracion' }
podman run --rm -v "${src}:/app:Z" -w /app localhost/argentgob-mvp-agent:latest python scripts/validate_corpus.py
if ($LASTEXITCODE -ne 0) { throw 'Fallo de corpus' }
```

Los paths `/app` y `tests/` del bloque son rutas **dentro del contenedor Linux**; paths host en Windows usan backslash. Los rebuilds adicionales de dashboard/migrator y redeploy siguen [consideraciones](../PROJECT_CONSIDERATIONS.md). Nunca reemplazar un fallo por ejecución local fuera del entorno acordado.

Para PG real usar red `argentgob-mvp_default` o red aislada de prueba y secreto montado/configurado sin mostrar DSN. `TEST_DATABASE_URL` debe apuntar a DB de prueba; no copiar una credencial en el comando/documento. Si las fixtures existentes leen variables distintas, reconciliarlas en AV-00. El comando sin DB de arriba puede omitir pruebas: **eso no cierra integración PG**.

Scripts existentes que se ejecutan según hito: `scripts/e2e_h8_console.py`, `scripts/e2e_fase4_predictive.py`, `scripts/e2e_fase5_evidence.py`, `scripts/demo_end_to_end.py --real` y `python -m argentgob.audit.cli`. Ejecutarlos dentro de un contenedor correctamente conectado/configurado. `--real` de la demo actual es PG, no LLM.

### A crear con la implementación

- Runners/archivos de la tabla inicial: declarar exactos selectors una vez existan y comprobar colección no vacía con `pytest --collect-only`.
- Gate agregado ligero propuesto `scripts/verify_agent_live.py --milestone AV-XX`: reutiliza pytest y scripts existentes, valida prerrequisitos y genera evidencia sanitizada; no framework de manifests nuevo. Es entregable de AV-01, ampliado en cada hito.
- `scripts/e2e_agent_live.py` para D-01…D-12: se entrega en AV-05, con selección por caso/grupo y proveedores explícitos.
- Marker opt-in `llm_local` o variable equivalente, registrado al agregar el primer test LLM. Hoy no existe ese marker en pyproject. No usarlo en comandos como si ya seleccionara pruebas.
- Gate real exige que tests LLM y PG requeridos hayan corrido y no quedado skipped. «No disponible» significa BLOCKED para el gate, no PASS. Unitarios pueden cerrarse sin red, el hito que exige E2E real no.

Elegir primero el subconjunto afectado, luego regresión completa de cierre. No inventar comandos Ruff/typing ejecutables si no están instalados/configurados; usar tooling del proyecto y registrar limitaciones.

## 4. Evidencia de cierre

Por gate, conservar en ubicación de evidencia acordada por el proyecto (fuera del repo para logs temporales) un resumen machine-readable sin payloads:

- `milestone_id`, `gate_id`, fecha UTC, commit/árbol verificado e imágenes/digests. Si hay cambios sin commit, hash del diff además de commit; repetir al cambiar el árbol.
- Precondiciones, dependencias cerradas y versiones reales de toolchain, Compose, CrewAI/modelo.
- IDs de casos/tests seleccionados, conteo descubierto, comandos seguros y exit codes; passed/failed/skipped con razón.
- Modos LLM/proveedores/DB y hashes de fixtures/configuración no secreta. No incluir entorno completo ni hash reutilizable de un secreto débil.
- Run/event/decision/approval IDs necesarios, contadores/oráculos, duración y diagnóstico seguro.
- Resultado `PASS|FAIL|BLOCKED`, limitaciones y ruta al resumen de sesión en [sessions](../sessions/).

El gate agregado genera PASS únicamente si todas las postcondiciones obligatorias pasan; fallo parcial no sobrescribe una evidencia PASS válida como si fuera del árbol actual. Cambios posteriores en contrato, código o test invalidan los gates dependientes. No crear un catálogo de cientos de archivos por caso: un reporte por hito y referencia a resultados reutilizados basta.

### Checklist final por hito

1. Entrada y dependencias con evidencia verificadas; todos sus tests obligatorios ejecutados.
2. Imágenes afectadas reconstruidas/redeployadas; smoke sin fuentes cuando corresponda.
3. Invariantes/contadores/no-leak y E2E del hito correctos; ningún skip crítico oculto.
4. Regresión/corpus según cambio y política de cierre del proyecto; fallos previos identificados, nunca reclasificados como resueltos sin evidencia.
5. Actualizar documentación afectada, estado en índice y resumen de sesión con límites.
6. Solo después, aplicar publicación/merge según el ciclo del proyecto. Esta sesión documental no publica ni certifica gates operativos.

## 5. Validación de esta entrega documental

No requiere rebuild de imágenes ni tests de aplicación. Sí: enlaces relativos existentes, nomenclatura AV y T/D consistente, todos los hitos con entrada/unidades/tests/gate/entregables, inexistencia de cambios en src/tests/compose/dependencias y comandos futuros etiquetados. El resultado de esta revisión documental no debe marcar AV-00 ni ningún gate de software como PASS.
