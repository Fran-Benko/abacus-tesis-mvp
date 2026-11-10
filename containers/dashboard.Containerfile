# containers/dashboard.Containerfile
FROM python:3.12-slim-bookworm

# Metadatos
LABEL org.opencontainers.image.title="argentgob-dashboard"
LABEL org.opencontainers.image.description="Dashboard de observabilidad de ArgentGob-Mesh (Streamlit)"

# Usuario no-root
RUN groupadd --gid 1001 argentgob && \
    useradd --uid 1001 --gid argentgob --create-home --home-dir /home/argentgob argentgob

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias primero (cache de capas)
COPY pyproject.toml README.md ./
RUN mkdir -p src/argentgob && touch src/argentgob/__init__.py
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copiar solo lo necesario para el dashboard
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY policies/ ./policies/
# AV-02 (T-A07): artefactos y corpus dentro de la imagen (sin bind mount).
COPY data/ ./data/

RUN chown -R argentgob:argentgob /app

USER argentgob

ENV RISK_MODEL_DIR=/app/data/models/risk_model_v1
ENV RISK_CORPUS_PATH=/app/data/corpus/corpus_v1.parquet

EXPOSE 8501

CMD ["streamlit", "run", "src/argentgob/dashboard/dashboard.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
