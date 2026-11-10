"""Verificación de la API de runs contra un servicio ya desplegado (AV-01).

Ejercita el contrato HTTP real: validación negativa, admisión, idempotencia,
cursor de eventos, aislamiento por actor y estado terminal. No imprime
payloads ni el informe.

Uso:
    python api_check.py --base http://127.0.0.1:8090 --asset AAPL
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TERMINAL = {"SUCCEEDED", "PARTIAL", "BLOCKED", "FAILED", "TIMED_OUT", "REJECTED", "EXPIRED", "INDETERMINATE"}


def _request(method: str, url: str, *, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={**(headers or {}), "content-type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except ValueError:
            return exc.code, {"raw": raw[:200]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verificación de la API de runs")
    parser.add_argument("--base", default="http://127.0.0.1:8090")
    parser.add_argument("--asset", default="AAPL")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)

    actor = {"x-argentgob-actor": "auditor-api"}
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    status, health = _request("GET", f"{args.base}/health")
    check("health 200", status == 200 and health.get("status") == "ok", str(status))
    status, ready = _request("GET", f"{args.base}/ready")
    check("ready 200", status == 200 and ready.get("ready") is True, str(status))

    status, _ = _request(
        "POST",
        f"{args.base}/runs",
        body={
            "scenario_id": "finanzas",
            "profile": "admin",
            "input": {"asset": args.asset},
            "idempotency_key": "api-check-admin",
        },
        headers=actor,
    )
    check("perfil admin rechazado (422)", status == 422, str(status))

    status, _ = _request(
        "POST",
        f"{args.base}/runs",
        body={
            "scenario_id": "finanzas",
            "profile": "analyst",
            "input": {"asset": args.asset, "url": "http://evil"},
            "idempotency_key": "api-check-extra",
        },
        headers=actor,
    )
    check("campo extra rechazado (422)", status == 422, str(status))

    status, _ = _request(
        "POST",
        f"{args.base}/runs",
        body={
            "scenario_id": "finanzas",
            "profile": "analyst",
            "input": {"asset": args.asset},
            "idempotency_key": "api-check-no-actor",
        },
    )
    check("actor ausente rechazado (401)", status == 401, str(status))

    body = {
        "scenario_id": "finanzas",
        "profile": "analyst",
        "input": {"asset": args.asset},
        "idempotency_key": f"api-check-{int(time.time())}",
    }
    status, created = _request("POST", f"{args.base}/runs", body=body, headers=actor)
    check("admisión 202", status == 202 and "run_id" in created, str(status))
    run_id = created.get("run_id", "")

    status, repeated = _request("POST", f"{args.base}/runs", body=body, headers=actor)
    check(
        "idempotencia devuelve el mismo run",
        status == 202 and repeated.get("run_id") == run_id,
        str(status),
    )

    status, _ = _request(
        "POST",
        f"{args.base}/runs",
        body={**body, "input": {"asset": "MSFT"}},
        headers=actor,
    )
    check("clave conflictiva 409", status == 409, str(status))

    status, _ = _request(
        "GET", f"{args.base}/runs/{run_id}", headers={"x-argentgob-actor": "otro"}
    )
    check("run de otro actor 404", status == 404, str(status))

    states: list[str] = []
    snapshot: dict = {}
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status, snapshot = _request("GET", f"{args.base}/runs/{run_id}", headers=actor)
        if status != 200:
            break
        if not states or states[-1] != snapshot.get("state"):
            states.append(snapshot.get("state", ""))
        if snapshot.get("state") in TERMINAL:
            break
        time.sleep(1)

    kinds = [event.get("kind") for event in snapshot.get("events", [])]
    check("estado terminal alcanzado", snapshot.get("state") in TERMINAL, str(states))
    check("evento run.accepted presente", "run.accepted" in kinds)
    check("evento run.finished presente", "run.finished" in kinds)
    check("decisión de gobernanza emitida", "governance.decided" in kinds)
    check(
        "sin argumentos operativos en eventos",
        "execution_arguments" not in json.dumps(snapshot.get("events", [])),
    )
    check(
        "resultado proyectado presente",
        snapshot.get("result") is not None,
        str(sorted(snapshot.get("result") or {})),
    )

    status, page = _request(
        "GET", f"{args.base}/runs/{run_id}?after_seq=0&limit=5", headers=actor
    )
    check(
        "página acotada por limit",
        status == 200 and len(page.get("events", [])) <= 5,
        str(len(page.get("events", []))),
    )

    print(
        json.dumps(
            {
                "run_id": run_id,
                "states": states,
                "final_state": snapshot.get("state"),
                "event_count": len(kinds),
                "event_kinds": sorted(set(kinds)),
                "result_keys": sorted(snapshot.get("result") or {}),
                "predictor_status": snapshot.get("predictor_status"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""))
    print(f"===== API AV-01: {len(checks) - len(failed)} passed, {len(failed)} failed =====")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
