"""Canonicalización del payload de auditoría y encadenamiento SHA-256 (H7).

El payload canónico contiene SOLO los campos normativos H7: tipo de evento,
entidad, digest, decisión/approval, outcome, actor permitido y timestamp.
Nunca contiene raw de argumentos, comentarios ni errores (INV-05).

La serialización canónica reutiliza la misma estrategia que el envelope
(`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`) para
que payloads equivalentes (mismo contenido, distinto orden de claves) produzcan
el mismo hash. No se usan serializadores ad hoc.

Encadenamiento: `chain_hash = sha256(prev_hash + "\\n" + payload)` sobre UTF-8.
Representación del hash: `sha256:` + 64 hex minúsculas (71 chars, cabe en
`String(80)`). Valor génesis: `GENESIS_HASH` (64 ceros con prefijo `sha256:`).
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# Valor génesis: la primera entrada encadena contra este hash.
GENESIS_HASH = "sha256:" + "0" * 64

# Campos canónicos normativos (el orden de serialización lo fija sort_keys).
_CANONICAL_FIELDS = (
    "event_type",
    "entity",
    "digest",
    "decision",
    "approval",
    "outcome",
    "actor",
    "timestamp",
)


def canonical_json(obj: dict[str, Any]) -> str:
    """Serializa un dict a JSON canónico (claves ordenadas, UTF-8, sin espacios)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_payload(
    *,
    event_type: str,
    entity: str,
    digest: str,
    outcome: str,
    actor: str,
    timestamp: datetime | None = None,
    decision: str | None = None,
    approval: str | None = None,
) -> str:
    """Construye el payload canónico (JSON) con los campos normativos H7.

    `actor` es el actor permitido (agent_id / auditor), nunca raw de argumentos.
    `timestamp` se normaliza a UTC e ISO-8601.
    """
    ts = timestamp or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    payload = {
        "event_type": event_type,
        "entity": entity,
        "digest": digest,
        "decision": decision,
        "approval": approval,
        "outcome": outcome,
        "actor": actor,
        "timestamp": ts.astimezone(timezone.utc).isoformat(),
    }
    return canonical_json(payload)


def chain_hash(prev_hash: str, payload: str) -> str:
    """SHA-256 de la concatenación no ambigua de `prev_hash` y `payload` (UTF-8)."""
    material = prev_hash + "\n" + payload
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def recompute_chain_hash(prev_hash: str, payload: str) -> str:
    """Recomputa el chain_hash normalizando el payload (para el verificador).

    Re-serializa el payload canónico (json.loads → canonical_json) para que
    payloads equivalentes (mismo contenido, distinto orden/espaciado) produzcan
    el mismo hash, mientras que un cambio de valor en cualquier campo canónico
    produce un hash distinto (detección de manipulación).
    """
    return chain_hash(prev_hash, canonical_json(json.loads(payload)))