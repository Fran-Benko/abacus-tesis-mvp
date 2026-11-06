"""
Tests del corpus sintético (Fase 2).

Verifican que el corpus versionado cumpla el esquema feature_schema_v1 y los
controles de calidad: ausencia de PII real, unicidad, completitud, dominio,
balance 50/30/20, ≥15% límite/conflicto y partición de evaluación congelada.

Si el corpus versionado no existe (p. ej. checkout sin `data/`), se genera un
corpus reducido en un directorio temporal para validar la lógica del generador
y del validador sin depender del artefacto.
"""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus" / "corpus_v1.parquet"
MANIFEST = ROOT / "data" / "corpus" / "corpus_manifest.json"
SCHEMA = ROOT / "docs" / "analytics" / "risk_dataset_schema.json"


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def corpus_df() -> pd.DataFrame:
    """Corpus versionado; si no existe, se genera uno reducido en tmp."""
    if CORPUS.exists():
        return pd.read_parquet(CORPUS)
    tmp = ROOT / "data" / "corpus"
    tmp.mkdir(parents=True, exist_ok=True)
    result = _run("build_synthetic_corpus.py", "--seed", "7", "--size", "300")
    assert result.returncode == 0, result.stderr
    return pd.read_parquet(CORPUS)


def test_schema_json_es_valido():
    """El JSON Schema del corpus debe ser JSON válido y declarar v1."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["version"] == "v1"
    assert "label" in schema["properties"]
    assert schema["properties"]["label"]["properties"]["source"]["const"] == "rubrica_humana"


def test_corpus_cumple_esquema_y_controles():
    """El validador debe pasar sobre el corpus versionado (exit 0)."""
    if not CORPUS.exists():
        pytest.skip("corpus versionado no presente")
    result = _run("validate_corpus.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULTADO: OK" in result.stdout


def test_corpus_sin_pii_real(corpus_df: pd.DataFrame):
    """Ninguna columna de features debe contener PII real."""
    from scripts.validate_corpus import _is_pii_real  # noqa: PLC0415

    for col in corpus_df.columns:
        if str(col).startswith(("context.", "payload.")):
            for value in corpus_df[col].dropna().astype(str).unique():
                assert not _is_pii_real(value), f"PII real en {col}: {value}"


def test_corpus_balance_y_split(corpus_df: pd.DataFrame):
    """Balance 50/30/20 y split 60/20/20 estratificado por clase."""
    counts = corpus_df["label.risk_class"].value_counts()
    total = len(corpus_df)
    assert counts.get("LOW", 0) / total >= 0.45
    assert counts.get("MEDIUM", 0) / total >= 0.25
    assert counts.get("HIGH", 0) / total >= 0.15

    split = corpus_df["split"].value_counts(normalize=True)
    assert abs(split.get("train", 0) - 0.60) < 0.05
    assert abs(split.get("calibration", 0) - 0.20) < 0.05
    assert abs(split.get("evaluation", 0) - 0.20) < 0.05


def test_corpus_dominios_y_completitud(corpus_df: pd.DataFrame):
    """Dominios del schema y ausencia de nulos."""
    assert corpus_df["context.tool_name"].isin(
        {"news", "stock_price", "crypto_price"}
    ).all()
    assert corpus_df["context.operation_class"].isin(
        {"READ", "EXTERNAL_SEND", "WRITE", "DELETE", "DDL", "CODE_EXEC"}
    ).all()
    assert corpus_df["context.profile"].isin(
        {"analyst", "analyst_restricted", "admin"}
    ).all()
    assert corpus_df["label.risk_class"].isin({"LOW", "MEDIUM", "HIGH"}).all()
    assert int(corpus_df.isnull().sum().sum()) == 0


def test_corpus_limite_conflicto_minimo(corpus_df: pd.DataFrame):
    """Al menos 15% de escenarios límite/conflicto."""
    sources = corpus_df["scenario_source"].value_counts()
    ratio = (sources.get("limite", 0) + sources.get("conflicto", 0)) / len(corpus_df)
    assert ratio >= 0.15


def test_particion_evaluacion_congelada(corpus_df: pd.DataFrame):
    """El hash de la partición evaluation coincide con el manifest."""
    if not MANIFEST.exists():
        pytest.skip("manifest no presente")
    from scripts.validate_corpus import evaluation_freeze_hash  # noqa: PLC0415

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["evaluation_partition_frozen"] is True
    assert manifest["evaluation_freeze_hash"] == evaluation_freeze_hash(corpus_df)


def test_generador_es_reproducible(tmp_path):
    """Misma seed => mismo hash de partici?n de evaluaci?n."""
    from scripts.validate_corpus import evaluation_freeze_hash  # noqa: PLC0415

    hashes = []
    for i in range(2):
        out = tmp_path / f"run{i}"
        result = _run(
            "build_synthetic_corpus.py", "--seed", "11", "--size", "300",
            "--out-dir", str(out),
        )
        assert result.returncode == 0, result.stderr
        df = pd.read_parquet(out / "corpus_v1.parquet")
        hashes.append(evaluation_freeze_hash(df))
    assert hashes[0] == hashes[1]

def test_etiqueta_anti_circular(corpus_df: pd.DataFrame):
    """La etiqueta declara fuente por rúbrica humana (no observed_action)."""
    assert (corpus_df["label.source"] == "rubrica_humana").all()
    assert corpus_df["label.rationale"].str.len().gt(0).all()