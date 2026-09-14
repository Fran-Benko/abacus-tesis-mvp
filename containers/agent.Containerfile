# containers/agent.Containerfile
FROM python:3.12-slim-bookworm

# Metadatos
LABEL org.opencontainers.image.title="argentgob-agent"
LABEL org.opencontainers.image.description="MVP del agente financiero gobernado ArgentGob-Mesh"

# Usuario no-root
RUN groupadd --gid 1001 argentgob && \
    useradd --uid 1001 --gid argentgob --create-home --home-dir /home/argentgob argentgob
WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias primero (cache de capas)
COPY pyproject.toml README.md ./
# Placeholder: pip -e necesita que src/ exista (egg_base) en este paso
RUN mkdir -p src/argentgob && touch src/argentgob/__init__.py
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[test]"

# Copiar código fuente y recursos
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY prompts/ ./prompts/
COPY policies/ ./policies/
COPY scripts/ ./scripts/
COPY alembic.ini ./
COPY tests/ ./tests/

# Cambiar propietario
RUN chown -R argentgob:argentgob /app

USER argentgob

# Por defecto: ejecutar el agente
CMD ["python", "-m", "argentgob.agent.main"]
