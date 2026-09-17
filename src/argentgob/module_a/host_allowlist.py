"""
Allowlist de hosts para R2 — Frontera gobernada.

Autoriza SOLO hosts exactos (sin sufijo amplio, sin auto-descubrimiento).
Cada egress verifica el host de destino contra la allowlist; un redirect a un
host no autorizado se bloquea (PROHIBITED_REDIRECT) antes del side effect.

ADR-0001: los hosts se autorizan como capa interna de la tool, no como campo
de política. La allowlist es un conjunto cerrado de hosts exactos.
"""
from __future__ import annotations

from urllib.parse import urlparse

from argentgob.core.errors import ReasonCode
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


class HostNotAllowedError(Exception):
    """El host de destino no está en la allowlist (R2)."""

    def __init__(self, host: str, reason: ReasonCode = ReasonCode.PROHIBITED_REDIRECT):
        self.host = host
        self.reason = reason
        super().__init__(f"host no autorizado: {host}")


class HostAllowlist:
    """Conjunto cerrado de hosts exactos autorizados para egress."""

    def __init__(self, hosts: set[str] | None = None):
        # Normalización mínima: minúsculas, sin puerto ni esquema.
        self._hosts = {self._normalize(h) for h in (hosts or set())}

    @staticmethod
    def _normalize(host: str) -> str:
        """Normaliza un host a minúsculas sin puerto."""
        h = host.strip().lower().rstrip(".")
        # Quitar el puerto si está presente (ej. "host:443" -> "host").
        if ":" in h:
            h = h.split(":", 1)[0]
        return h

    def add(self, host: str) -> None:
        """Agrega un host exacto a la allowlist."""
        self._hosts.add(self._normalize(host))

    def allows(self, host: str) -> bool:
        """True si el host exacto está autorizado (sin sufijo amplio)."""
        return self._normalize(host) in self._hosts

    def host_from_url(self, url: str) -> str:
        """Extrae el host de una URL. Lanza si la URL es inválida."""
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise HostNotAllowedError(url)
        return host

    def check_url(self, url: str) -> str:
        """Verifica que el host de la URL esté autorizado.

        Retorna el host normalizado si está permitido; lanza
        HostNotAllowedError (PROHIBITED_REDIRECT) en caso contrario.
        """
        host = self.host_from_url(url)
        if not self.allows(host):
            log.warning(
                "host_not_allowed",
                host=host,
                reason=ReasonCode.PROHIBITED_REDIRECT.value,
            )
            raise HostNotAllowedError(host)
        return host