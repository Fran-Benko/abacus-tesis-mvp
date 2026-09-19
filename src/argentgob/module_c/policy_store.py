"""Carga y caché limitada de políticas ABAC desde PostgreSQL (Unidad 3).

La caché local guarda versión, digest, `loaded_at`, `valid_until` y TTL. Su
vigencia efectiva no supera la de la policy. Ante DB caída solo una lectura
explícita, idempotente y habilitada para caché vigente puede continuar por esta
fuente; las escrituras/deletes bloquean (fail-closed).

INV-11: ante duda, falla cerrada (BLOCK). No se autoriza escritura offline.
"""
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from argentgob.core.config import Settings
from argentgob.core.errors import GovernanceAction, Obligation
from argentgob.module_c.policy import GovernancePolicy
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

DEFAULT_POLICY_TTL_SECONDS = 30  # Norma propone 30 s como inicio calibrable.


@dataclass
class PolicyCacheEntry:
    """Entrada de caché de políticas con metadata de vigencia."""

    version: int
    digest: str
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: datetime | None = None
    ttl_seconds: float = DEFAULT_POLICY_TTL_SECONDS
    policies: list[GovernancePolicy] = field(default_factory=list)


class PolicyStore:
    """Lee políticas desde PostgreSQL y las mantiene en una caché limitada."""

    def __init__(self, settings: Settings, ttl_seconds: float | None = None):
        self.settings = settings
        self.ttl_seconds = ttl_seconds or DEFAULT_POLICY_TTL_SECONDS
        self._cache: PolicyCacheEntry | None = None
        self._lock = threading.Lock()
        self._db_available = True

    # ── Carga vigente ────────────────────────────────────────────────
    def load_current(self) -> list[GovernancePolicy]:
        """Retorna las políticas vigentes, con caché limitada.

        Una entrada de caché vigente se usa tal cual si su `valid_until` aún no
        venció y el TTL no expiró. Una entrada vencida fuerza una recarga desde
        DB; si la DB no está disponible, se usa la caché solo si sigue vigente
        según su `valid_until` (efectividad acotada por la policy).
        """
        now = datetime.now(timezone.utc)
        cached = self._get_cached(now)
        if cached is not None:
            return cached

        try:
            fresh = self._load_from_db()
            entry = PolicyCacheEntry(
                version=self._schema_version(),
                digest=self._digest(fresh),
                loaded_at=now,
                valid_until=self._next_valid_until(fresh),
                ttl_seconds=self.ttl_seconds,
                policies=fresh,
            )
            with self._lock:
                self._cache = entry
            self._db_available = True
            log.info("policy_cache_loaded", count=len(fresh))
            return fresh
        except Exception as exc:  # noqa: BLE001 - fail-closed ante DB caída
            self._db_available = False
            log.warning("policy_cache_db_unavailable", detail=str(exc))
            stale = self._cached_within_validity(now)
            if stale is not None:
                return stale
            raise

    # ── Caché ────────────────────────────────────────────────────────
    def _get_cached(self, now: datetime) -> list[GovernancePolicy] | None:
        with self._lock:
            cached = self._cache
        if cached is None:
            return None
        # TTL expirado
        ttl_deadline = cached.loaded_at.timestamp() + cached.ttl_seconds
        if now.timestamp() > ttl_deadline:
            return None
        # Vigencia de la policy más corta vence el TTL efectivo
        if cached.valid_until is not None and now > cached.valid_until:
            return None
        return cached.policies

    def _cached_within_validity(self, now: datetime) -> list[GovernancePolicy] | None:
        """Caché que siga vigente según su `valid_until`, ignorando el TTL."""
        with self._lock:
            cached = self._cache
        if cached is None:
            return None
        if cached.valid_until is not None and now > cached.valid_until:
            return None
        return cached.policies

    def invalidate(self) -> None:
        """Invalida la caché (forzar recarga en la próxima consulta)."""
        with self._lock:
            self._cache = None

    @property
    def db_available(self) -> bool:
        """Estado de disponibilidad de la DB de la última operación."""
        return self._db_available

    # ── Carga desde PostgreSQL ───────────────────────────────────────
    def _load_from_db(self) -> list[GovernancePolicy]:
        from argentgob.db.connection import get_connection

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        policy_id, policy_version, profile_name, tool_name,
                        operation_class, environment, resource, effect,
                        priority, sensitivity_limit, obligations, transform_spec,
                        valid_from, valid_until, policy_digest
                    FROM governance_policies
                    WHERE valid_from <= now()
                      AND (valid_until IS NULL OR valid_until > now())
                    """
                )
                rows = cur.fetchall()
            return [self._row_to_policy(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_policy(row) -> GovernancePolicy:
        (
            policy_id, policy_version, profile_name, tool_name,
            operation_class, environment, resource, effect,
            priority, sensitivity_limit, obligations, transform_spec,
            valid_from, valid_until, policy_digest,
        ) = row
        obligations_list = PolicyStore._parse_json_list(obligations)
        transform_dict = PolicyStore._parse_json_object(transform_spec)
        return GovernancePolicy(
            policy_id=policy_id,
            policy_version=policy_version,
            profile_name=profile_name,
            tool_name=tool_name,
            operation_class=operation_class,
            environment=environment,
            resource=resource,
            effect=GovernanceAction(effect),
            priority=priority,
            sensitivity_limit=sensitivity_limit,
            obligations=[Obligation(o) for o in obligations_list],
            transform_spec=transform_dict,
            valid_from=valid_from,
            valid_until=valid_until,
            policy_digest=policy_digest,
        )

    @staticmethod
    def _parse_json_list(value: str | None) -> list:
        """Parse un campo JSON TEXT; ante inválido asume vacío (fail-soft)."""
        if not value:
            return []
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            log.warning("policy_json_list_invalid", field="obligations")
            return []
        return parsed if isinstance(parsed, list) else []
    
    @staticmethod
    def _parse_json_object(value: str | None) -> dict | None:
        """Parse un campo JSON TEXT; ante inválido asume None (fail-soft)."""
        if not value:
            return None
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            log.warning("policy_json_object_invalid", field="transform_spec")
            return None
        return parsed if isinstance(parsed, dict) else None
    
    # ── Helpers de metadata ──────────────────────────────────────────
    def _schema_version(self) -> int:
        return 2  # Schema H5

    @staticmethod
    def _digest(policies: list[GovernancePolicy]) -> str:
        """Digest de la lista de políticas (para detectar corrupción/invalidación)."""
        canonical = json.dumps(
            [
                {
                    "policy_id": p.policy_id,
                    "policy_version": p.policy_version,
                    "tool_name": p.tool_name,
                    "profile_name": p.profile_name,
                    "effect": p.effect.value,
                }
                for p in policies
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _next_valid_until(policies: list[GovernancePolicy]) -> datetime | None:
        """El `valid_until` más próximo entre las políticas, o None."""
        candidates = [p.valid_until for p in policies if p.valid_until is not None]
        if not candidates:
            return None
        return min(candidates)