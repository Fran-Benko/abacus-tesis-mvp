"""Hold idempotente de argumentos en memoria para la aprobación humana (Unidad 1).

Mientras una ejecución está en espera de aprobación humana, el sistema debe
conservar los argumentos originales para poder re-ejecutarla tras el `resume`.
Este hold es:

- **Idempotente**: dada la misma `event_id` y el mismo digest, recuperar el
  mismo argumento; el mismo evento con un digest distinto se REHUSA (HoldConflict).
- **Acotado**: el tamaño de un argumento no supera `max_held_argument_bytes`, y
  el total acumulado no supera `max_held_total_bytes` (evita memoria sin límite).
- **Vigencia acotada**: el hold expira con `hold_ttl_seconds` (≤ el timeout de
  aprobación), y se evicta al resolver/terminar el flujo.

El hold es in-process: no sobrevive un reinicio real. Por eso Unidad 5 exige
que, tras un reinicio, si no hay hold, la ejecución quede en
`arguments_unavailable` y NO se consuma el token ni se auto-reintente.
"""
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from argentgob.core.config import Settings
from argentgob.hitl.errors import HoldCapacityError, HoldConflictError
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


def sha256_hex(payload: bytes) -> str:
    """Digest hex de un payload (los argumentos canónicos del hold)."""
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(args: dict[str, Any]) -> bytes:
    """Serialización canónica y estable de los argumentos para el digest."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


class InMemoryArgumentHold:
    """Hold en memoria, thread-safe e idempotente, acotado por bytes y TTL."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._entries: dict[str, dict[str, Any]] = {}
        # Digest del argumento (para idempotencia por evento -> digest).
        self._digest_by_event: dict[str, str] = {}
        self._lock = threading.Lock()
        self._total_bytes = 0

    # ── Público ──────────────────────────────────────────────────────
    def hold(
        self,
        *,
        event_id: str,
        tool_name: str,
        agent_id: str,
        argument: dict[str, Any],
        expires_at: datetime,
    ) -> str:
        """Registra los argumentos de un evento pendiente de aprobación.

        Idempotente por (event_id, digest): una segunda llamada con los mismos
        argumentos devuelve la misma `hold_id`. Un mismo evento con un digest
        distinto es un conflicto y se rehúsa (no se pisa el argumento original,
        para no corromper la evidencia sobre la que se basó una aprobación).
        """
        payload = _canonical_bytes(argument)
        size = len(payload)
        if size > self.settings.max_held_argument_bytes:
            raise HoldCapacityError(
                f"argumento de {size} bytes excede max_held_argument_bytes="
                f"{self.settings.max_held_argument_bytes}",
                reason=HoldCapacityError.reason,
            )
        digest = sha256_hex(payload)

        now = datetime.now(timezone.utc)
        self._evict_expired(now)

        with self._lock:
            existing_digest = self._digest_by_event.get(event_id)
            if existing_digest is not None:
                if existing_digest == digest:
                    entry = self._entries[event_id]
                    log.info(
                        "hold_idempotent_hit",
                        event_id=event_id,
                        hold_id=entry["hold_id"],
                    )
                    return entry["hold_id"]
                raise HoldConflictError(
                    f"event_id {event_id} ya tiene argumentos con otro digest",
                    reason=HoldConflictError.reason,
                )

            if self._total_bytes + size > self.settings.max_held_total_bytes:
                raise HoldCapacityError(
                    f"hold lleno: {self._total_bytes}+{size} > max_held_total_bytes="
                    f"{self.settings.max_held_total_bytes}",
                    reason=HoldCapacityError.reason,
                )

            hold_id = str(uuid.uuid4())
            entry = {
                "hold_id": hold_id,
                "event_id": event_id,
                "tool_name": tool_name,
                "agent_id": agent_id,
                "argument": argument,
                "digest": digest,
                "size_bytes": size,
                "held_at": now,
                "expires_at": expires_at,
            }
            self._entries[event_id] = entry
            self._digest_by_event[event_id] = digest
            self._total_bytes += size
            return hold_id

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Retorna el argumento retenido para un evento, o None si no existe."""
        self._evict_expired(datetime.now(timezone.utc))
        with self._lock:
            entry = self._entries.get(event_id)
        if entry is None:
            return None
        # Devolver una copia para que el consumidor no mute el hold.
        return dict(entry["argument"])

    def get_with_digest(self, event_id: str) -> tuple[dict[str, Any], str] | None:
        """Argumento + digest para verificaciones que exigen el digest original."""
        self._evict_expired(datetime.now(timezone.utc))
        with self._lock:
            entry = self._entries.get(event_id)
        if entry is None:
            return None
        return dict(entry["argument"]), entry["digest"]

    def release(self, event_id: str) -> None:
        """Libera el hold de un evento cuando el flujo termina."""
        with self._lock:
            entry = self._entries.pop(event_id, None)
            self._digest_by_event.pop(event_id, None)
            if entry is not None:
                self._total_bytes -= entry["size_bytes"]

    def evict_expired(self) -> int:
        """Purga entradas vencidas; devuelve cuántas se eliminaron."""
        now = datetime.now(timezone.utc)
        return self._evict_expired(now)

    # ── Interno ──────────────────────────────────────────────────────
    def _evict_expired(self, now: datetime) -> int:
        expired = [
            eid
            for eid, entry in self._entries.items()
            if entry["expires_at"] <= now
        ]
        for eid in expired:
            self.release(eid)
        if expired:
            log.info("hold_evicted", count=len(expired))
        return len(expired)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def size(self) -> int:
        return len(self._entries)