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
# AV-02 (T-A07): artefactos del predictor y corpus congelado dentro de la
# imagen, de modo que un contenedor sin bind mount de fuentes siga teniendo
# `RiskPredictor.available=True` y evidencia academica reproducible.
COPY data/ ./data/

# Cambiar propietario
RUN chown -R argentgob:argentgob /app

USER argentgob

# AV-02: la imagen sirve la API interna de runs por defecto. La CLI financiera
# sigue disponible con `python -m argentgob.agent.main <ticker> --profile ...`.
ENV RISK_MODEL_DIR=/app/data/models/risk_model_v1
ENV RISK_CORPUS_PATH=/app/data/corpus/corpus_v1.parquet

EXPOSE 8090

CMD ["python", "-m", "argentgob.live.server"]
