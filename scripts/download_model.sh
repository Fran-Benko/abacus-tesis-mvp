#!/usr/bin/env bash
# scripts/download_model.sh
# Descarga el modelo Llama 3.1 8B Instruct (Q4_K_M) en ./models/
# Requiere: ~4.7 GB de espacio libre y conexión a internet

set -euo pipefail

MODEL_DIR="./models"
MODEL_FILE="Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
MODEL_URL="https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/${MODEL_FILE}"

mkdir -p "${MODEL_DIR}"

if [ -f "${MODEL_DIR}/${MODEL_FILE}" ]; then
    echo "Modelo ya descargado en ${MODEL_DIR}/${MODEL_FILE}"
    exit 0
fi

echo "Descargando ${MODEL_FILE} (~4.7 GB)..."
echo "   Esto puede tardar varios minutos segun tu conexion."
curl -L --progress-bar -o "${MODEL_DIR}/${MODEL_FILE}" "${MODEL_URL}"
echo "Modelo descargado exitosamente."
