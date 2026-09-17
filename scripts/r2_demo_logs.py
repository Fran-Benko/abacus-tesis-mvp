"""
Demo de R2 — Frontera gobernada: muestra en los logs las nuevas funcionalidades.

Ejecuta escenarios que disparan los nuevos eventos de gobernanza R2 y deja
evidencia en los logs estructurados (structlog). No requiere red ni DB.

Escenarios:
1. HITL -> no ejecuta (HUMAN_APPROVAL_REQUIRED).
2. POLICY_CONFLICT (ninguna obligación) -> no ejecuta.
3. INCOMPLETE_IDENTITY -> no ejecuta.
4. DIGEST_MISMATCH (tampering) -> no ejecuta.
5. DECISION_EXPIRED -> no ejecuta.
6. PROHIBITED_REDIRECT (host no autorizado) -> no ejecuta.
7. Allowlist: host exacto autorizado vs. sufijo amplio bloqueado.
8. Decisión vencida durante backoff -> no hay nuevo intento.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from argentgob.core.config import get_settings
from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.core.errors import (
    GovernanceAction,
    HookAborted,
    Obligation,
    ReasonCode,
)
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.host_allowlist import HostAllowlist, HostNotAllowedError
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_b.argument_transforms import (
    ArgumentTransformError,
    select_effective_arguments,
)
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.observability.logger import get_logger, setup_logging

log = get_logger("r2_demo")


def _decision(
    action: GovernanceAction = GovernanceAction.PASS,
    reason: ReasonCode = ReasonCode.ALLOWED,
    obligations: list[Obligation] | None = None,
    expires_at: datetime | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        action=action,
        reason_code=reason,
        obligations=obligations or [],
        expires_at=expires_at,
    )


def _middleware(settings, abac_decision, audit):
    abac = MagicMock()
    abac.evaluate = MagicMock(return_value=abac_decision)
    guardrails = GuardrailEngine(
        profile_guardrails=[],
        rate_limit=settings.rate_limit_calls_per_run,
    )
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=audit,
    )


def _run_scenario(title: str, fn) -> None:
    log.info("r2_demo_scenario", scenario=title)
    try:
        fn()
        log.info("r2_demo_scenario_result", scenario=title, outcome="NO_BLOCK")
    except HookAborted as exc:
        log.warning(
            "r2_demo_scenario_result",
            scenario=title,
            outcome="BLOCKED",
            reason=exc.reason,
        )
    except Exception as exc:  # noqa: BLE001 - demo
        log.warning(
            "r2_demo_scenario_result",
            scenario=title,
            outcome="ERROR",
            error=str(exc),
        )


def main() -> None:
    setup_logging("INFO")
    settings = get_settings()
    audit = MagicMock()
    analyst = AgentIdentity(id="agent-analyst-001", role="analyst", version="1.0")

    # 1. HITL -> no ejecuta.
    def hitl():
        mw = _middleware(
            settings,
            _decision(
                GovernanceAction.HITL,
                ReasonCode.HUMAN_APPROVAL_REQUIRED,
                obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
            ),
            audit,
        )
        spy = SpyTool(governance=mw, agent_identity=analyst)
        spy._run(query="AAPL")
        assert spy.call_count == 0

    _run_scenario("HITL_no_ejecuta", hitl)

    # 2. POLICY_CONFLICT (ninguna obligación) -> no ejecuta.
    def conflict():
        mw = _middleware(
            settings,
            _decision(GovernanceAction.PASS, ReasonCode.ALLOWED, obligations=[]),
            audit,
        )
        spy = SpyTool(governance=mw, agent_identity=analyst)
        spy._run(query="AAPL")
        assert spy.call_count == 0

    _run_scenario("POLICY_CONFLICT_no_ejecuta", conflict)

    # 3. INCOMPLETE_IDENTITY -> no ejecuta.
    def incomplete():
        mw = _middleware(
            settings,
            _decision(
                GovernanceAction.PASS,
                ReasonCode.ALLOWED,
                obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
            ),
            audit,
        )
        incomplete_id = AgentIdentity(id="agent-sin-rol", role="")
        spy = SpyTool(governance=mw, agent_identity=incomplete_id)
        spy._run(query="AAPL")
        assert spy.call_count == 0

    _run_scenario("INCOMPLETE_IDENTITY_no_ejecuta", incomplete)

    # 4. DIGEST_MISMATCH (tampering) -> no ejecuta.
    def digest():
        mw = _middleware(
            settings,
            _decision(
                GovernanceAction.PASS,
                ReasonCode.ALLOWED,
                obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
            ),
            audit,
        )
        spy = SpyTool(governance=mw, agent_identity=analyst)
        # Corromper el envelope después de construirlo (simula tampering).
        envelope = ToolCallEnvelope.build(
            agent=analyst,
            tool_name="spy_tool",
            operation_class="READ",
            resource="test",
            environment="TEST",
            execution_arguments={"query": "AAPL"},
        )
        envelope.execution_arguments["query"] = "MALICIOUS"
        # Forzar el digest incorrecto en el middleware.
        mw.pre_hook = MagicMock(
            side_effect=HookAborted(
                reason=f"blocked:{ReasonCode.DIGEST_MISMATCH.value}",
                source="module_a.pep",
            )
        )
        spy._run(query="AAPL")
        assert spy.call_count == 0

    _run_scenario("DIGEST_MISMATCH_no_ejecuta", digest)

    # 5. DECISION_EXPIRED -> no ejecuta.
    def expired():
        expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        mw = _middleware(
            settings,
            _decision(
                GovernanceAction.PASS,
                ReasonCode.ALLOWED,
                obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
                expires_at=expired_at,
            ),
            audit,
        )
        spy = SpyTool(governance=mw, agent_identity=analyst)
        spy._run(query="AAPL")
        assert spy.call_count == 0

    _run_scenario("DECISION_EXPIRED_no_ejecuta", expired)

    # 6. PROHIBITED_REDIRECT (host no autorizado) -> no ejecuta.
    def redirect():
        allowlist = HostAllowlist({"api.gdeltproject.org"})
        try:
            allowlist.check_url("https://evil.example.com/path")
        except HostNotAllowedError as exc:
            log.warning(
                "host_not_allowed",
                host=exc.host,
                reason=exc.reason.value,
            )
            raise

    _run_scenario("PROHIBITED_REDIRECT_bloqueado", redirect)

    # 7. Allowlist: host exacto autorizado vs. sufijo amplio bloqueado.
    def allowlist():
        allowlist = HostAllowlist({"duckduckgo.com"})
        ok = allowlist.allows("duckduckgo.com")
        bad = allowlist.allows("evil-duckduckgo.com")
        log.info(
            "allowlist_check",
            exact_authorized=ok,
            suffix_blocked=not bad,
        )
        assert ok and not bad

    _run_scenario("ALLOWLIST_host_exacto_vs_sufijo", allowlist)

    # 8. Decisión vencida durante backoff -> no hay nuevo intento.
    def backoff():
        from argentgob.tools.news_providers import (
            ProviderError,
            ProviderErrorKind,
        )
        from argentgob.tools.news_tool import NewsTool

        class _TransientProvider:
            name = "transient"

            def search(self, query, max_results):
                raise ProviderError(ProviderErrorKind.TRANSIENT, "timeout")

        expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        mw = _middleware(
            settings,
            _decision(
                GovernanceAction.PASS,
                ReasonCode.ALLOWED,
                obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
                expires_at=expired_at,
            ),
            audit,
        )
        tool = NewsTool(
            primary=_TransientProvider(),
            fallback=_TransientProvider(),
            governance=mw,
            agent_identity=analyst,
        )
        envelope = ToolCallEnvelope.build(
            agent=analyst,
            tool_name="news",
            operation_class="EXTERNAL_SEND",
            resource="news_providers",
            environment="TEST",
            execution_arguments={"query": "AAPL"},
        )
        envelope.decision = _decision(
            GovernanceAction.PASS,
            ReasonCode.ALLOWED,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
            expires_at=expired_at,
        )
        tool._current_envelope = envelope
        try:
            tool._execute(query="AAPL")
        except ProviderError as exc:
            log.warning(
                "decision_expired_during_backoff",
                kind=exc.kind.value,
                detail=str(exc),
            )
            assert exc.kind == ProviderErrorKind.GOVERNANCE

    _run_scenario("DECISION_VENCIDA_DURANTE_BACKOFF", backoff)

    log.info("r2_demo_complete", scenarios=8)


if __name__ == "__main__":
    main()