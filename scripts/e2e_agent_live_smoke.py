"""Evidencia sanitizada del smoke real de AV-01 (llama.cpp + PostgreSQL).

Ejecuta un run real por el supervisor y publica un resumen sin payloads:
estado final, tipos de evento, conteos y latencias. No imprime argumentos,
prompts ni el informe completo.

Uso (dentro de la red del stack):
    python scripts/e2e_agent_live_smoke.py --asset AAPL
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from argentgob.live.supervisor import RunSupervisor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke real AV-01 (sanitizado)")
    parser.add_argument("--asset", default="AAPL")
    parser.add_argument("--profile", default="analyst")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)

    supervisor = RunSupervisor(run_timeout_seconds=args.timeout)
    started = time.monotonic()
    try:
        record, created = supervisor.submit(
            actor_id="auditor-e2e",
            scenario_id="finanzas",
            profile=args.profile,
            input_payload={"asset": args.asset},
            idempotency_key=f"av01-e2e-{int(time.time())}",
        )
        finished = supervisor.wait(record.run_id, timeout=args.timeout)
        snapshot = supervisor.snapshot(record.run_id, actor_id="auditor-e2e")
    finally:
        supervisor.shutdown()

    kinds = [event["kind"] for event in snapshot["events"]]
    decisions = [
        event
        for event in snapshot["events"]
        if event["kind"] == "governance.decided"
    ]
    evidence = {
        "run_id": snapshot["run_id"],
        "created": created,
        "state": finished.state,
        "terminal_reason": finished.terminal_reason,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "event_count": len(kinds),
        "event_kinds": kinds,
        "decisions": [
            {
                "action": event.get("action"),
                "reason_codes": event.get("reason_codes"),
                "tool": event.get("tool"),
                "latency_ms": event.get("latency_ms"),
            }
            for event in decisions
        ],
        "result_present": snapshot["result"] is not None,
        "result_keys": sorted(snapshot["result"]) if snapshot["result"] else [],
        "predictor_status": snapshot["predictor_status"],
        "truncated": snapshot["truncated"],
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2))

    ok = (
        finished.state in ("SUCCEEDED", "BLOCKED", "PARTIAL")
        and "run.accepted" in kinds
        and "run.finished" in kinds
        and bool(decisions)
    )
    print(f"===== E2E AV-01: {'OK' if ok else 'FAIL'} =====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
