# Observaciones — Mejoras para futuras versiones

Estado: En revisión (hallazgos de la prueba integral E2E, no bloqueantes).
Fecha: 2026-09-17
Rama: `agents/plan-implementacion-avancemos`

Este documento registra observaciones surgidas de la prueba integral end-to-end
del agente (análisis de `AAPL` con perfil `analyst`). Son situaciones que
funcionan correctamente según el diseño actual, pero que podrían mejorarse en
una futura versión. No son bugs ni bloqueantes.

## 1. El agente hace múltiples llamadas redundantes a la misma tool

### Observación

En una sola corrida, el agente llamó a `news` **5 veces** (más 2 a
`stock_price` y 2 a `crypto_price`), a pesar de que la primera llamada a
`news` ya devolvía noticias. El perfil `analyst` tiene `max_tool_calls=5`, y el
agente agotó todo el presupuesto en llamadas redundantes a `news`, dejando poco
margen para las demás tools.

### Causa raíz

El LLM local (`llama-3.1-8b-instruct`) **no tiene function calling nativo**. En
`crew.py` se fuerza `llm.supports_function_calling = lambda: False`, lo que
hace que CrewAI use el modo de "tool calling por texto": el LLM genera texto
que CrewAI parsea para invocar tools. Este modo es impreciso:

- El LLM repite llamadas a la misma tool porque no recibe feedback estructurado
  de que ya la usó.
- Tiende a re-llamar para "confirmar" o porque el parseo de la respuesta
  anterior no fue perfecto.

### Mejora propuesta (futura versión)

- Usar un modelo con function calling nativo (p.ej. un modelo más capaz o un
  servidor que exponga tool calling real).
- Ajustar el prompt del agente para que no repita tools ya consultadas.
- Considerar deduplicación de llamadas a la misma tool con los mismos args
  dentro de una corrida.

## 2. GDELT no responde desde este entorno (timeout)

### Observación

GDELT (`api.gdeltproject.org`) da **timeout de lectura** desde este entorno.
Probado directamente desde el container:

```
EXC: ReadTimeout HTTPSConnectionPool(host='api.gdeltproject.org', port=443): Read timed out. (read timeout=10)
```

Por cada llamada a `news`, GDELT falla 4 veces (attempt 0,1,2,3) con
`kind=TRANSIENT` y recién entonces cae al fallback DuckDuckGo. En la corrida se
registraron **12 fallos GDELT** y **8 fallos DDG**.

### Causa raíz

Es un problema de **red/entorno**, no de código. El host `api.gdeltproject.org`
no responde desde esta red (probablemente bloqueado o inaccesible). El código
clasifica correctamente el timeout como `TRANSIENT`, reintenta con backoff y
hace fallback a DDG — el diseño R1 funciona como se espera.

### Impacto práctico

En este entorno, cada llamada a `news` tarda ~50s (4 timeouts de 10s + backoff)
antes de caer a DDG. Con 5 llamadas a `news`, esto explica la lentitud de la
corrida.

### Mejora propuesta (futura versión)

- Reducir el timeout de GDELT (p.ej. 3-5s) para acelerar el fallback a DDG
  cuando GDELT no responde.
- Reducir `max_attempts` para GDELT en entornos sin red.
- Configurar GDELT como opcional/deshabilitable vía env var cuando se sabe que
  no hay red.
- Investigar si el problema de red es específico (p.ej. probar con otro host o
  DNS).

## 3. Nota: el fallback DDG también falla a veces

### Observación

DuckDuckGo (fallback) también registró fallos transitorios (8 en la corrida),
aunque eventualmente devolvió noticias. El paquete `duckduckgo_search` emite un
`RuntimeWarning` indicando que fue renombrado.

### Mejora propuesta (futura versión)

- Migrar al paquete renombrado de DuckDuckGo para eliminar el warning.
- Revisar la política de rate-limit de DDG para reducir fallos transitorios.