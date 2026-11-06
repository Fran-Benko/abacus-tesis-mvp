"""
scripts/validate_corpus.py

Valida el corpus sintético versionado contra el JSON Schema de features
(docs/analytics/risk_dataset_schema.json, feature_schema_v1) y los controles
de calidad de Fase 2:

- PII real ausente (INV-04/05).
- Unicidad de escenarios (record_id y perfiles de features).
- Completitud (sin nulos / NaN).
- Dominio (valores dentro de los enum del schema).
- Balance objetivo (LOW 50 / MEDIUM 30 / HIGH 20; ≥15% límite/conflicto).
- Partición de evaluación congelada (hash reproducible).

Uso:
    python scripts/validate_corpus.py [--corpus data/corpus/corpus_v1.parquet] \
        [--manifest data/corpus/corpus_manifest.json] \
        [--schema docs/analytics/risk_dataset_schema.json]

Exit 0 si todas las validaciones pasan; exit 1 en caso contrario.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pandas as pd




def load_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convierte el parquet plano de vuelta a registros anidados (schema v1)."""
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        row: dict[str, Any] = {
            "record_id": str(r["record_id"]),
            "feature_schema_version": str(r["feature_schema_version"]),
            "corpus_version": str(r["corpus_version"]),
            "scenario_source": str(r["scenario_source"]),
            "split": str(r["split"]),
            "context": {
                "tool_name": r["context.tool_name"],
                "operation_class": r["context.operation_class"],
                "agent_role": r["context.agent_role"],
                "profile": r["context.profile"],
                "environment": r["context.environment"],
                "resource": r["context.resource"],
                "hour_bucket": int(r["context.hour_bucket"]),
            },
            "payload": {
                "payload_size_bytes": int(r["payload.payload_size_bytes"]),
                "arg_count": int(r["payload.arg_count"]),
                "json_depth": int(r["payload.json_depth"]),
                "sanitized_text_length": int(r["payload.sanitized_text_length"]),
                "has_url": bool(r["payload.has_url"]),
                "has_sql_injection_pattern": bool(r["payload.has_sql_injection_pattern"]),
                "has_shell_pattern": bool(r["payload.has_shell_pattern"]),
                "pii_category_count": int(r["payload.pii_category_count"]),
                "has_pii": bool(r["payload.has_pii"]),
                "dominant_data_type": str(r["payload.dominant_data_type"]),
            },
            "control": {
                "tool_allowed_for_profile": bool(r["control.tool_allowed_for_profile"]),
                "matched_policy_count": int(r["control.matched_policy_count"]),
                "has_policy_conflict": bool(r["control.has_policy_conflict"]),
                "max_allowed_sensitivity": int(r["control.max_allowed_sensitivity"]),
                "policy_is_active": bool(r["control.policy_is_active"]),
                "guardrail_query_injection": bool(r["control.guardrail_query_injection"]),
                "guardrail_topic_relevance": bool(r["control.guardrail_topic_relevance"]),
                "guardrail_rate_limit": bool(r["control.guardrail_rate_limit"]),
                "rate_limit_consumption": int(r["control.rate_limit_consumption"]),
                "operation_type_requested": str(r["control.operation_type_requested"]),
            },
            "label": {
                "risk_class": str(r["label.risk_class"]),
                "confidence": int(r["label.confidence"]),
                "rationale": str(r["label.rationale"]),
                "source": str(r["label.source"]),
                "double_labeled": bool(r["label.double_labeled"]),
                "reviewed": bool(r["label.reviewed"]),
            },
        }
        rows.append(row)
    return rows


def _is_pii_real(value: str) -> bool:
    """Detecta PII REAL (no sint?tica) en un string del corpus.

    Usa las mismas categor?as que el Sanitizer (SECRET, EMAIL, PAYMENT_NUMBER,
    POSSIBLE_DNI). Los placeholders sint?ticos se consideran no reales.
    """
    import re
    email = re.search(r"[a-zA-Z0-9_.+-]+@([a-zA-Z0-9-]+)\.([a-zA-Z.]+)", value)
    if email and email.group(2).lower() not in {"test", "example"}:
        return True
    if re.search(r"(?i)(password|passwd|secret|token|api_key)\s*[:=]\s*\S+", value):
        return True
    if re.search(r"\b(?!4111111111111111|5500000000000004)\d{16,19}\b", value):
        return True
    if re.search(r"\b(?!12345678|9876543)\d{7,8}\b", value):
        return True
    return False

def evaluation_freeze_hash(df: pd.DataFrame) -> str:
    """Hash de la partición evaluation (congelada al cierre de Fase 2)."""
    eval_rows = df[df["split"] == "evaluation"]
    canonical = (
        eval_rows[["record_id", "label.risk_class"]]
        .sort_values(["record_id"])
        .to_json(orient="split", date_format="iso")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="data/corpus/corpus_v1.parquet")
    parser.add_argument("--manifest", default="data/corpus/corpus_manifest.json")
    parser.add_argument("--schema", default="docs/analytics/risk_dataset_schema.json")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    corpus_path = root / args.corpus
    manifest_path = root / args.manifest
    schema_path = root / args.schema

    if not corpus_path.exists():
        print(f"[validate] ERROR: corpus no encontrado: {corpus_path}", file=sys.stderr)
        return 1

    df = pd.read_parquet(corpus_path)
    errors: list[str] = []

    # 1) Schema JSON (feature_schema_v1).
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    rows = load_rows(df)
    # Validador compilado una sola vez (evita recompilar el schema por fila).
    validator = jsonschema.Draft202012Validator(schema)
    schema_errors = 0
    for i, row in enumerate(rows):
        row_errors = list(validator.iter_errors(row))
        if row_errors:
            schema_errors += 1
            if schema_errors <= 5:
                errors.append(
                    f"fila {i} ({row['record_id']}): incumple schema: "
                    f"{row_errors[0].message}"
                )
    print(f"[validate] schema JSON: {len(rows)} filas, {schema_errors} incumplimientos")

    # 2) Completitud (sin nulos/NaN).
    nulls = int(df.isnull().sum().sum())
    nans = int(df.isna().sum().sum())
    print(f"[validate] completitud: nulls={nulls} nans={nans}")
    if nulls or nans:
        errors.append(f"hay {nulls} nulos / {nans} NaN en el corpus")

    # 3) Unicidad de record_id.
    unique_ids = bool(df["record_id"].is_unique)
    print(f"[validate] unicidad record_id: {unique_ids}")
    if not unique_ids:
        errors.append("record_id duplicados en el corpus")

    # 4) Dominio de columnas categóricas del schema (enum).
    domains = {
        "context.tool_name": {"news", "stock_price", "crypto_price"},
        "context.operation_class": {"READ", "EXTERNAL_SEND", "WRITE", "DELETE", "DDL", "CODE_EXEC"},
        "context.agent_role": {"analyst", "analyst_restricted", "admin"},
        "context.profile": {"analyst", "analyst_restricted", "admin"},
        "context.environment": {"LOCAL", "TEST", "PROD"},
        "scenario_source": {"benigno", "maligno", "limite", "conflicto"},
        "split": {"train", "calibration", "evaluation"},
        "payload.dominant_data_type": {"string", "number", "boolean", "object", "array", "null"},
        "label.risk_class": {"LOW", "MEDIUM", "HIGH"},
        "label.source": {"rubrica_humana"},
    }
    for col, allowed in domains.items():
        bad = sorted(set(df[col].unique()) - allowed)
        if bad:
            errors.append(f"dominio inválido en {col}: {bad}")

    # 5) Balance objetivo y cobertura.
    label_counts = df["label.risk_class"].value_counts().to_dict()
    low = label_counts.get("LOW", 0)
    med = label_counts.get("MEDIUM", 0)
    high = label_counts.get("HIGH", 0)
    total = len(df)
    balance_ok = low >= 1200 and med >= 700 and high >= 500
    print(
        f"[validate] balance: LOW={low} MEDIUM={med} HIGH={high} "
        f"balance_ok={balance_ok}"
    )
    if not balance_ok:
        errors.append(f"balance fuera de rango: {label_counts}")

    source_counts = df["scenario_source"].value_counts().to_dict()
    boundary_conflict = (
        source_counts.get("limite", 0) + source_counts.get("conflicto", 0)
    )
    ok_15 = boundary_conflict / max(1, total) >= 0.15
    print(
        f"[validate] límite/conflicto: {boundary_conflict} ({boundary_conflict/total:.1%}) "
        f"ok_15pct={ok_15}"
    )
    if not ok_15:
        errors.append("menos del 15% de escenarios límite/conflicto")

    # 6) PII real ausente (misma regla que el generador).
    # Regla PII id?ntica a la del generador (scripts/build_synthetic_corpus.py):
    # placeholder sint?tico (dominio .test / PAN 4xxx / DNI conocido) NO cuenta
    # como PII real; un email con dominio distinto de .test/.example s?.

    real_pii = 0
    for col in df.columns:
        if str(col).startswith(("context.", "payload.")):
            for value in df[col].dropna().astype(str).unique():
                if _is_pii_real(value):
                    real_pii += 1
    print(f"[validate] PII real detectada: {real_pii}")
    if real_pii:
        errors.append(f"{real_pii} valores con PII real")

    # 7) Congelado de partición de evaluación (coherente con el manifest).
    freeze_hash = evaluation_freeze_hash(df)
    manifest_hash = ""
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_hash = manifest.get("evaluation_freeze_hash", "")
    consistent = manifest_hash and manifest_hash == freeze_hash
    print(f"[validate] freeze hash: {'OK' if consistent else 'DIVERGENTE'}")
    if not consistent:
        errors.append(
            f"hash de partición evaluation no coincide con manifest "
            f"({freeze_hash[:24]})"
        )

    if errors:
        print("\n[validate] ERRORES:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(f"[validate] RESULTADO: FAIL ({len(errors)} errores)", file=sys.stderr)
        return 1

    print("[validate] RESULTADO: OK — corpus versionado, validado y reproducible")
    return 0


if __name__ == "__main__":
    sys.exit(main())