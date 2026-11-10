# Sesión 0028 — AV-03: UI financiera en vivo

**Fecha:** 2026-11-10
**Rama:** `agents/av-03-ui-financiera-en-vivo` (base `ebdce28`, AV-02)
**Hito:** AV-03 — UI financiera en vivo
**Gate:** G-AV-03 **PASS**

---

## 1. Objetivo

Habilitar una sección nueva del dashboard que opere la API interna de runs
(AV-01/AV-02) con LLM local real, sin sustituir la demo determinista existente y
sin exponer payload crudo, consola raw ni argumentos operativos.

Unidades del plan cubiertas:

1. Sección nueva en Operaciones y Demo sin sustituir la demo actual; actor/perfil
   permitido y campo financiero acotado.
2. Session state conserva intención/run/cursor; `POST` solo por intención
   explícita; polling de región activa, no refresco global bloqueante.
3. Timeline distingue tool, decisión, ejecución, contenido, parcialidad y HITL
   pendiente; informe saneado y link de evidencia.
4. Manejo de API caída, recarga, dos sesiones, expiración/truncado; sin admin ni
   consola raw.

## 2. Implementación

| Archivo | Rol |
|---|---|
| `src/argentgob/dashboard/live_client.py` | Cliente HTTP (`/ready`, `POST /runs`, `GET /runs/{id}`) con token de servicio montado, actor por cabecera y errores traducidos a mensajes seguros |
| `src/argentgob/dashboard/live_session.py` | Lógica pura de sesión: clave de idempotencia determinista, cursor incremental, polling detenido en terminal, categorías del timeline, aprobaciones pendientes, nota de parcialidad |
| `src/argentgob/dashboard/pages/live_finance.py` | Vista Streamlit: readiness, formulario de intención, región activa en `st.fragment(run_every="2s")`, timeline filtrable, informe saneado, evidencia por IDs |
| `src/argentgob/dashboard/dashboard.py` | Registra la página `live-finance` |
| `compose.yaml` | Monta `service_token` en el dashboard y exporta `AGENT_API_SERVICE_TOKEN_FILE` |
| `scripts/verify_live_ui_av03.py` | Gate G-AV-03 contra el stack desplegado |
| `scripts/verify_agent_live.py` | Selectores y preflight de AV-03 |
| `tests/unit/test_live_session.py` | 20 tests de la lógica de sesión |
| `tests/unit/test_live_client.py` | 11 tests del cliente HTTP |
| `tests/unit/test_live_finance_apptest.py` | 10 tests AppTest de la vista |
| `tests/integration/test_live_ui_flow.py` | 4 tests UI → API → supervisor → hijo real |
| `tests/unit/_live_finance_app.py` | Entrypoint mínimo para `AppTest.from_file` |

### Decisiones de diseño

- **Idempotencia por intención, no por clic.** La clave se deriva de
  `(actor, escenario, perfil, activo, consulta)` con SHA-256 truncado y prefijo
  `ui-`. Un doble clic, un rerun del script o una recarga reconstruyen la misma
  clave y la API devuelve el run existente; el texto de la consulta nunca viaja
  como clave.
- **Polling acotado a la región activa.** `st.fragment(run_every="2s")` refresca
  solo el bloque del run; el resto de la página no se re-ejecuta y el `POST` no
  se repite.
- **Terminal detiene el sondeo.** `LiveSession.terminal` usa `TERMINAL_STATES`
  del contrato; en estado terminal `poll()` retorna sin llamar a la API.
- **API caída no borra la sesión.** Un fallo de transporte conserva `run_id` y
  cursor, marca `api_unavailable` y permite reintentar el sondeo sin reenviar la
  admisión.
- **HITL nunca se aprueba desde la vista.** `approval.pending` sin
  `approval.resolved` se lista como pendiente y la UI remite a Auditoría y HITL.
- **Sin consola raw.** El timeline solo usa campos de la allowlist ya saneados
  por el servidor; no hay `st.code` de logs ni argumentos operativos.

## 3. Evidencia

### 3.1 Gate G-AV-03 (`scripts/verify_live_ui_av03.py`)

Ejecutado dentro de `argentgob-mvp_default` contra el stack desplegado con
llama.cpp real:

```
[OK] ready.checks · {"catalogo_sembrado": true, "db_reachable": true, "schema_en_head": true, "service_token_configured": true}
[OK] ready.llm · llm_reachable
[OK] ready.cupo · capacity_free
[OK] admisión · run admitido
[OK] visibilidad.incremental · 0.01s · estado RUNNING
[OK] run.terminal · PARTIAL
[OK] run.resultado · informe proyectado
[OK] eventos.orden · 65 eventos
[OK] cursor.final · cursor 65
[OK] polling.detenido · sin sondeo tras terminal
[OK] idempotencia.clave · misma clave
[OK] idempotencia.run · mismo run
[OK] actor.ajeno · El run no existe o no es visible para este actor.
[OK] timeline.sin.raw · 65 filas
[OK] timeline.categorias · HITL, decisión, ejecución, herramienta, run
[OK] ui.render · sin excepciones
[OK] ui.sin.raw · sin payload crudo
[OK] demo.anterior · scripts/demo_end_to_end.py

===== G-AV-03: 18 passed, 0 failed =====
```

### 3.2 Gate de hito (`scripts/verify_agent_live.py --milestone AV-03 --with-llm`)

```
"collected": 109,
"runs": [{ "exit_code": 0, "summary": "109 passed in 23.91s" }],
"llm_runs": [{ "exit_code": 0, "summary": "1 passed in 82.09s (0:01:22)" }],
"result": "PASS"
```

### 3.3 Recorrido de navegador (LLM real)

`http://localhost:8501/live-finance`, actor `operador-1`, perfil `analyst`,
activo `AAPL`:

| Momento | Estado observado |
|---|---|
| +4 s | `RUNNING`, run `b000322f`, 2 eventos, cursor 2, 2 sondeos |
| +64 s | `RUNNING`, 64 eventos, cursor 64, 40 sondeos, aprobaciones pendientes visibles |
| +154 s | `PARTIAL`, 65 eventos, cursor 65, 41 sondeos, sondeo detenido, informe real de AAPL con limitaciones y tabla de evidencia |

El informe mostrado incluye precio, variación, volumen, resumen de noticias
(con errores de proveedor explícitos), análisis y consideraciones de riesgo; no
aparece ningún argumento operativo ni traza.

### 3.4 Regresión

| Prueba | Resultado |
|---|---|
| Suite completa | **473 passed + 10 skipped** |
| PG real (`test_postgres_real.py`) | 7/7 sobre `argentgob_av03_test` |
| E2E H8 | 23/23 sobre `argentgob_av03_h8` |
| E2E Fase 4 | 6/6 |
| E2E Fase 5 | 19/19 |
| Corpus | `RESULTADO: OK` |
| Demo determinista | 5 escenarios (`OK`, `BLOCK` ×3, `HITL COMPLETO`) |
| CLI financiera con LLM real | `status=SUCCESS` |
| Cadena de auditoría | `OK: la cadena de auditoría es íntegra.` |
| AppTest del dashboard con DB | 2/2 |
| Ruff (archivos nuevos/modificados) | `All checks passed!` |

Los 10 skips son los mismos de AV-02: 7 de PG real sin `TEST_DATABASE_URL`, 2 de
AppTest sin `DATABASE_URL` y 1 de LLM sin `DATABASE_URL`; todos se ejecutaron
por separado con las variables correspondientes.

## 4. Limitaciones

- El escenario financiero real cierra `PARTIAL` cuando el predictor eleva
  tentativas a HITL: es la regla de agregación del contrato §4, no un fallo.
- La UI no resuelve aprobaciones; el resume es AV-05.
- El estado de runs vive en memoria del proceso padre: un reinicio del servicio
  deja la sesión de UI sin run recuperable y la vista lo informa sin simular
  recovery.
- La sesión de UI conserva hasta 500 eventos; el anillo del servidor sigue siendo
  la fuente autoritativa con `truncated`/`first_available_seq`.
- La vista no expone consola raw ni argumentos operativos por diseño (INV-05).

## 5. Próximo hito

**AV-04 — Fronteras PII e IPI**, que requiere G-AV-00 y G-AV-01 PASS (ambos
cerrados) y no depende de tener los cinco dominios completos.
