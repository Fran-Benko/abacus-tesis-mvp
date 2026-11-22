# Despliegue público sin agente ni LLM local

**Fecha:** 2026-11-21. **Estado:** verificado localmente en proyecto aislado.

Este documento responde a una pregunta concreta: **¿se puede desplegar la
solución en GitHub sin el agente ni `llama-server`?** La respuesta corta es
**sí para el dashboard**, y **no para el agente con LLM real** en el plan
gratuito.

---

## 1. Qué funciona sin agente ni LLM (verificado)

Se detuvieron `argentgob-mvp-agent-1` y `argentgob-mvp-llama-server-1` y se
recorrieron las seis vistas del dashboard:

| Vista | Sin agente ni LLM | Evidencia |
|---|---|---|
| Executive Overview | **Funciona** | 715 decisiones, 253 BLOCK (35,4%), latencia 42,04 ms, cadena íntegra |
| Auditoría y HITL | **Funciona** | 444 decisiones, filtros, detalle sanitizado, resolución |
| Inteligencia Predictiva | **Funciona** | 598 predicciones, 134 HITL, 20 BLOCK, latencia 21,93 ms |
| Salud del Modelo | **Funciona** | PSI 0,2389 (moderada), 19 features comparables |
| Operaciones y Demo | **Funciona** | Demo determinista exit 0, 5 escenarios, persiste en PostgreSQL |
| Finanzas en vivo | **Degrada con aviso** | "Servicio de runs: API no disponible" (no rompe) |

La demo de Operaciones **no usa LLM**: `scripts/demo_lib.py` no importa
`crewai` ni llama a `llama-server`. Solo necesita PostgreSQL y los artefactos
del predictor, que ya están dentro de la imagen (`COPY data/`).

El dashboard tampoco importa `crewai`: sus dependencias reales son
`streamlit`, `pandas`, `psycopg`, `requests`, `pyyaml` y `scikit-learn`.

---

## 2. Artefactos agregados

| Archivo | Propósito |
|---|---|
| `compose.demo.yaml` | PostgreSQL + inicializador + dashboard. Sin `agent` ni `llama-server`. Publica `8501:8501` (no loopback) para que un proxy lo alcance |
| `.devcontainer/devcontainer.json` | Codespace con Python 3.12 + Docker-in-Docker, puerto 8501 público |
| `.devcontainer/setup-demo.sh` | Crea `.env`, `.secrets/*` y valida artefactos. **No descarga el GGUF** |
| `.devcontainer/start-demo.sh` | `docker compose -f compose.demo.yaml up -d --build` + espera de health |

### Uso local

```bash
python scripts/init_secrets.py
podman compose -f compose.demo.yaml up -d --build
# http://localhost:8501
```

### Uso en Codespaces

1. Crear un codespace desde el repo (botón **Code → Codespaces → Create**).
2. `postCreateCommand` prepara secretos; `postStartCommand` levanta el stack.
3. Abrir la pestaña **PORTS** → puerto 8501 → visibilidad **Public**.

`start-demo.sh` pasa el UID/GID del Codespace a Compose para que el migrador y
el dashboard lean los secretos montados, que permanecen con permisos `0600`.

---

## 3. Opciones de despliegue gratuito en GitHub

### 3.1 GitHub Codespaces — **la única opción viable**

| Aspecto | Valor |
|---|---|
| Cuota gratuita (plan Free) | **120 h de cómputo/mes** + 15 GB-mes de almacenamiento |
| Cuota gratuita (plan Pro) | 180 h/mes + 20 GB-mes |
| Máquina por defecto | 2 cores / 8 GB RAM / 32 GB disco |
| Vida máxima de un codespace | **12 h** (luego se detiene) |
| Idle timeout por defecto | 30 min (configurable) |
| Puertos | Forwarding con URL `https://<codespace>-8501.app.github.dev`; visibilidad pública disponible |
| Docker | Sí, con la feature `docker-in-docker` (necesaria para `compose.demo.yaml`) |

**Limitaciones reales para este proyecto:**

- **No hay GPU.** La opción de máquina con GPU existió solo en un trial cerrado
  y no está disponible en general. Por eso `llama-server` con
  `--n-gpu-layers -1` no puede correr; habría que usar la imagen CPU
  (`ghcr.io/ggml-org/llama.cpp:server`) con `--n-gpu-layers 0`, y aun así un
  Llama 3.1 8B Q4_K_M (~4,9 GB) en 8 GB de RAM con 2 cores sería
  impracticable para una demo interactiva.
- **El GGUF no está en el repo** (`.gitignore` lo excluye). Habría que
  descargarlo en cada codespace (4,9 GB) y el almacenamiento gratuito es de
  15 GB-mes.
- **El codespace se apaga a las 12 h** y por inactividad: no es un hosting
  permanente, es un entorno de demo efímero.
- **Consumo de cuota:** 120 h/mes alcanzan para ~10 sesiones de 12 h. Una demo
  de tesis entra holgada, pero no es un servicio 24/7.

### 3.2 GitHub Pages — **no sirve**

GitHub Pages solo publica **HTML, CSS y JavaScript estáticos**. No ejecuta
Python, no levanta Streamlit y no tiene base de datos. Descartado.

### 3.3 GitHub Actions — **no sirve como hosting**

Los runners son efímeros (máximo 35 días por workflow, en la práctica minutos u
horas) y no exponen puertos públicos. Serviría para CI (tests, build de
imágenes), no para servir la app. Nota: los runners estándar en repos
**públicos** son gratis e ilimitados, así que un workflow de tests no consume
cuota.

### 3.4 Alternativas fuera de GitHub (si se quiere URL permanente)

| Opción | Gratis | Nota |
|---|---|---|
| Streamlit Community Cloud | Sí | Requiere repo público; sin PostgreSQL persistente gratuito; la app necesita DB |
| Hugging Face Spaces | Sí (CPU) | Docker Spaces permite `compose`; sin GPU gratis |
| Fly.io / Railway / Render | Capa gratuita limitada | Permiten Postgres gestionado; el plan free duerme la app |

Ninguna resuelve el LLM local: para eso hace falta GPU, que no es gratuita en
ningún proveedor.

---

## 4. Recomendación

**Para la demo de la tesis:** usar **Codespaces** con `compose.demo.yaml`.

- Cubre 5 de 6 vistas con datos reales y la demo determinista completa.
- La vista Finanzas en vivo queda visible pero degradada con aviso explícito,
  lo que además demuestra el comportamiento fail-closed del diseño.
- Costo: $0 dentro de la cuota de 120 h/mes.
- El stack completo con LLM real se sigue mostrando en la máquina local con
  GPU (procedimiento de `AGENTS.md` §4).

**Si se necesita el agente con LLM en la nube:** no hay opción gratuita. Haría
falta una VM con GPU (p. ej. AWS g4dn, Azure NC-series) o un proveedor de
inferencia por API, lo que cambia el contrato del prototipo (LLM local).

---

## 5. Verificación ejecutada

Proyecto aislado `argentgob-demo-test` con `compose.demo.yaml` en puerto 8599:

| Check | Resultado |
|---|---|
| `podman compose -f compose.demo.yaml config` | exit 0 |
| `postgres` | Up (healthy) |
| `migrator` | Exited (0), revisión 012 aplicada |
| `dashboard` | Up (healthy), `/_stcore/health` → `ok` |
| Executive Overview | Renderiza con 0 decisiones (base nueva) |
| Operaciones y Demo | **exit 0**, 5 escenarios OK/BLOCK/HITL |
| Finanzas en vivo | Degrada con aviso, sin excepción |

El proyecto de prueba se eliminó con `down -v`; el stack principal
(`argentgob-mvp`) quedó intacto y healthy.
