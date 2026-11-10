"""Entrypoint del servicio HTTP interno de runs (AV-01).

Un worker ASGI. No publica puertos fuera de la red interna del proyecto; el
dashboard (AV-03) lo consume por `AGENT_API_BASE`.

Uso:
    python -m argentgob.live.server
"""
from __future__ import annotations

import os
import sys

from argentgob.observability.logger import setup_logging


def main() -> int:
    """Arranca uvicorn con un único worker."""
    import uvicorn

    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    host = os.environ.get("AGENT_API_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_API_PORT", "8090"))
    uvicorn.run(
        "argentgob.live.api:app",
        host=host,
        port=port,
        workers=1,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
