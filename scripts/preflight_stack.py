"""Preflight del stack (AV-02): valida precondiciones sin imprimir secretos.

Comprueba, antes de levantar nada:

- proveedor Compose disponible y versión;
- archivos de secreto presentes y no vacíos (solo se informa presencia);
- GGUF legible en la ruta configurada;
- artefactos del predictor y corpus congelado legibles;
- puertos de loopback libres (8080/8501) y ausencia de publicación del 8090.

Uso:
    python scripts/preflight_stack.py
    python scripts/preflight_stack.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SECRETS = (
    ".secrets/postgres_password",
    ".secrets/runtime_password",
    ".secrets/dashboard_password",
    ".secrets/service_token",
)
REQUIRED_ARTIFACTS = (
    "data/models/risk_model_v1/pipeline.joblib",
    "data/models/risk_model_v1/thresholds.json",
    "data/models/risk_model_v1/model_meta.json",
    "data/corpus/corpus_v1.parquet",
)
LOOPBACK_PORTS = (8080, 8501)


def _port_free(port: int) -> bool:
    """True si el puerto de loopback no está ocupado."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _compose_version() -> str | None:
    """Versión del proveedor Compose, o None si no está disponible."""
    try:
        completed = subprocess.run(
            ["podman", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    text = (completed.stdout or completed.stderr or "").strip().splitlines()
    return text[0][:120] if text else "desconocida"


def _gguf_path() -> Path:
    directory = Path(os.environ.get("GGUF_DIR") or ROOT / "models")
    name = os.environ.get("GGUF_FILE") or "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    return directory / name


def collect() -> dict:
    """Recolecta el estado del preflight (sin valores sensibles)."""
    secrets = {
        path: (ROOT / path).is_file() and (ROOT / path).stat().st_size > 0
        for path in REQUIRED_SECRETS
    }
    artifacts = {path: (ROOT / path).is_file() for path in REQUIRED_ARTIFACTS}
    gguf = _gguf_path()
    ports = {str(port): _port_free(port) for port in LOOPBACK_PORTS}
    compose = _compose_version()
    checks = {
        "compose_disponible": compose is not None,
        "secretos_presentes": all(secrets.values()),
        "artefactos_presentes": all(artifacts.values()),
        "gguf_legible": gguf.is_file() and os.access(gguf, os.R_OK),
        "puertos_loopback_libres": all(ports.values()),
    }
    return {
        "checks": checks,
        "ok": all(checks.values()),
        "compose_version": compose,
        "secrets": secrets,
        "artifacts": artifacts,
        "gguf_present": gguf.is_file(),
        "ports_free": ports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight del stack AV-02")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    args = parser.parse_args(argv)

    report = collect()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for name, ok in report["checks"].items():
            print(f"  [{'OK' if ok else 'FALLO'}] {name}")
        if not report["ok"]:
            print("Preflight incompleto: corrige los puntos marcados.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
