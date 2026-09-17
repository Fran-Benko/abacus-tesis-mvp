# ADR-0001 — R1: Contrato de la tool de noticias (GDELT + DDG fallback)

Estado: Aprobado (decisión técnica para R1 del plan MVP-0.3 ampliado).
Fecha: 2026-09-16
Rama: `agents/plan-implementacion-avancemos`

## Contexto

El plan R1 exige GDELT como proveedor primario y DuckDuckGo como fallback para
la búsqueda de noticias. El master-plan contiene contradicciones que deben
resolverse explícitamente antes de escribir código, sin inventar PASS.

## Decisiones

### 1. Renombrar la tool a `news` con resource genérico

La tool actual se llama `duckduckgo_news` con `resource="duckduckgo.com"`. Con
GDELT como primario, ese nombre y recurso serían engañosos. Se renombra la tool
a `news` con `resource="news_providers"` (genérico), actualizando de forma
cohesionada: registro, políticas, perfiles, guardrails, prompts, seed y tests.

Los hosts exactos de los proveedores (GDELT y DDG) se autorizan como capa
interna de la tool, no como campo de policy. No se descubre ni autoriza hosts
nuevos automáticamente.

### 2. Clasificación `EXTERNAL_SEND` con excepción acotada de retries

La norma prohíbe retries para `EXTERNAL_SEND`, pero el usuario solicita retries
de noticias. Se conserva la clasificación `EXTERNAL_SEND` y se resuelve una
excepción explícita y acotada: retries permitidos solo para consultas
idempotentes a proveedores permitidos (GDELT/DDG), con presupuesto máximo de 4
intentos por proveedor. No se reclasifica como READ solo por usar GET ni se
habilitan retries de envíos generales.

### 3. Normalización mínima y transformación

La normalización mínima de `query` es eliminar únicamente espacios al inicio y
final. No se altera casing, operadores, comillas, términos ni campos
adicionales. La selección y transformación gobernadas se aplican según las
reglas comunes (PEP existente).

### 4. Destinos y autorización de hosts

- GDELT primario, DDG fallback. Google News queda fuera del MVP.
- Endpoints fijos HTTPS, sin URLs arbitrarias del agente.
- Verificar redirects y solicitudes auxiliares antes de cada salida; deshabilitar
  redirects si no pueden validarse.
- No aceptar autorización por sufijo amplio ni descubrir hosts automáticamente.
- URLs de artículos son datos: devolverlas no autoriza visitarlas.

## Consecuencias

- Cambio de contrato público de la tool: `duckduckgo_news` → `news`.
- Requiere actualizar conjuntamente: `news_tool.py`, `profiles.py`, políticas
  YAML, `seed_policies.py`, guardrails, prompts y tests.
- El ABACEvaluator actual evalúa solo por `tool_name`; el resource genérico no
  cambia la evaluación de policy en este hito.