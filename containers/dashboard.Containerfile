# containers/dashboard.Containerfile
FROM python:3.12-slim-bookworm

# Metadatos
LABEL org.opencontainers.image.title="argentgob-dashboard"
LABEL org.opencontainers.image.description="Dashboard de observabilidad de ArgentGob-Mesh (Streamlit)"

# Usuario no-root
RUN groupadd --gid 1001 argentgob && \
    useradd --uid 1001 --gid argentgob --no-create-home argentgob

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias primero (cache de capas)
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copiar solo lo necesario para el dashboard
COPY src/ ./src/

RUN chown -R argentgob:argentgob /app

USER argentgob

EXPOSE 8501

CMD ["streamlit", "run", "src/argentgob/dashboard/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
