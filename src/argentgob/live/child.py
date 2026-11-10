"""Proceso hijo de un run (AV-01): entrada ejecutable del worker.

Se lanza como `python -m argentgob.live.child` por el supervisor. Recibe su
contexto por variables de entorno (nunca por argv, para no exponer datos en la
tabla de procesos) y publica eventos por un canal IPC explícito: un socket
loopback autenticado con un token de un solo uso.

Reglas:
- stdout/stderr del hijo se descartan: no se convierten en eventos (AV-I06).
- Solo se envían mensajes JSON de esquema cerrado.
- El hijo no decide gobernanza: el PEP del propio proceso lo hace.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any

from argentgob.live.runner import ChildContext, run_financial_child


class SocketChannel:
    """Canal de escritura JSON-lines sobre un socket ya conectado."""

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._file = sock.makefile("w", encoding="utf-8", newline="\n")

    def put(self, message: dict[str, Any], timeout: float | None = None) -> None:
        """Envía un mensaje JSON; `timeout` se ignora (socket bloqueante)."""
        self._file.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        try:
            self._file.close()
        finally:
            self._sock.close()


def _connect(port: int, token: str, *, timeout: float = 10.0) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    sock.settimeout(None)
    handshake = json.dumps({"type": "hello", "token": token}) + "\n"
    sock.sendall(handshake.encode("utf-8"))
    return sock


def main() -> int:
    """Punto de entrada del hijo: conecta, ejecuta el run y cierra el canal."""
    run_id = os.environ.get("ARGENTGOB_RUN_ID", "")
    port = int(os.environ.get("ARGENTGOB_IPC_PORT", "0"))
    token = os.environ.get("ARGENTGOB_IPC_TOKEN", "")
    if not run_id or port <= 0 or not token:
        return 2

    ctx = ChildContext(
        run_id=run_id,
        profile=os.environ.get("ARGENTGOB_PROFILE", "analyst"),
        asset=os.environ.get("ARGENTGOB_ASSET", ""),
        request=os.environ.get("ARGENTGOB_REQUEST") or None,
        max_event_bytes=int(os.environ.get("ARGENTGOB_MAX_EVENT_BYTES", "2048")),
    )
    try:
        sock = _connect(port, token)
    except OSError:
        return 3

    channel = SocketChannel(sock)
    try:
        run_financial_child(ctx, channel)
    finally:
        channel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
