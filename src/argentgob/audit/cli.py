"""CLI de verificación de la cadena de auditoría (H7, Unidad 2).

Uso: python -m argentgob.audit.cli [--db-url URL]

Verifica la cadena desde el génesis. Exit 0 solo si la cadena es íntegra;
exit no-cero con la primera ubicación (sequence) y un motivo sanitizado si se
detecta manipulación. No repara ni recalcula silenciosamente la historia.
"""
import argparse
import sys

import psycopg

from argentgob.audit.verify import (
    AuditChainVerifier,
    AuditVerificationError,
    REASON_DUPLICATE_HASH,
    REASON_HASH_MISMATCH,
    REASON_MISSING_DECISION,
    REASON_MISSING_EVENT,
    REASON_PREV_HASH_MISMATCH,
    REASON_SEQUENCE_GAP,
)
from argentgob.db.connection import get_db_url, normalize_db_url
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

# Motivo sanitizado → mensaje legible (sin datos crudos, INV-05).
_REASON_MESSAGES = {
    REASON_SEQUENCE_GAP: "secuencia con saltos (manipulación)",
    REASON_PREV_HASH_MISMATCH: "enlace prev-hash roto (manipulación)",
    REASON_HASH_MISMATCH: "hash recalculado no coincide (manipulación)",
    REASON_DUPLICATE_HASH: "hash duplicado (manipulación)",
    REASON_MISSING_EVENT: "event_id referenciado no existe",
    REASON_MISSING_DECISION: "decision_id referenciado no existe",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ArgentGob-Mesh · Verificador de la cadena de auditoría"
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="URL de conexión a PostgreSQL (default: DATABASE_URL del entorno)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Verifica la cadena y retorna el código de salida (0 = íntegra)."""
    args = _build_parser().parse_args(argv)

    def _factory():
        url = normalize_db_url(args.db_url) if args.db_url else get_db_url()
        return psycopg.connect(url, autocommit=True)

    verifier = AuditChainVerifier(connection_factory=_factory)
    try:
        ok, reason, sequence = verifier.verify()
    except AuditVerificationError as exc:
        log.error("audit_verify_error", detail=exc.message)
        print(f"ERROR: {exc.message}", file=sys.stderr)
        return 2

    if ok:
        print("OK: la cadena de auditoría es íntegra.")
        return 0

    message = _REASON_MESSAGES.get(reason, reason or "desconocido")
    print(
        f"INVALID: cadena manipulada en sequence={sequence} motivo={message}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())