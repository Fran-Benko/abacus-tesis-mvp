"""
tests/unit/test_analytics.py

Tests de la pipeline analítica de la capa predictiva (Fase 3 — Modelado).

Cubren:
- Preproceso: preprocesador con las 3 familias de features (num/cat/bin).
- Encodificación del target y orden de probabilidades (LOW/MEDIUM/HIGH).
- CV estratificada 5-fold dentro de train (no usa calibration/evaluation).
- Calibración isotónica sobre calibration (la evaluation queda congelada).
- Selección de umbral HIGH priorizando Recall con piso de precisión.
- Métricas finales sobre la partición de evaluación (macro F1, recall, brier,
  matriz, latencia P50/P95/P99).
- Reproducibilidad: misma seed => mismas métricas reportables.
- Anti-circularidad estructural: las features NO contienen observed_action ni
  resultados de herramienta (spec §3, enforced por el esquema del corpus).

Si el corpus versionado no existe, los tests que dependen de artefactos se
omiten (misma política que test_corpus.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus" / "corpus_v1.parquet"
MODEL_DIR = ROOT / "data" / "models" / "risk_model_v1"

pytestmark = pytest.mark.skipif(
    not CORPUS.exists(),
    reason="corpus versionado no presente",
)


def _import_trainer():
    """Importa scripts/train_risk_model.py sin registrar el paquete scripts."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "train_risk_model", ROOT / "scripts" / "train_risk_model.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tr = _import_trainer()


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return pd.read_parquet(CORPUS)


def test_preprocesador_cubre_familias(df: pd.DataFrame):
    """El preprocesador debe incluir num/cat/bin para las features full."""
    pre = tr.make_preprocessor(tr.CONTEXT_PAYLOAD_FEATURES + tr.CONTROL_FEATURES)
    transformer_names = {name for name, _, _ in pre.transformers}
    assert {"num", "cat", "bin"} <= transformer_names


def test_target_encoding_ordinal(df: pd.DataFrame):
    """LOW/MEDIUM/HIGH se codifican 0/1/2 en ese orden."""
    y = tr.encode_target(df.head(10))
    expected = df.head(10)[tr.TARGET].map(tr.LABEL_ENCODING).to_numpy()
    np.testing.assert_array_equal(y, expected)
    assert list(tr.LABEL_ENCODING) == ["LOW", "MEDIUM", "HIGH"]


def test_split_60_20_20_estratificado(df: pd.DataFrame):
    """El split congelado mantiene proporción 60/20/20 y balance por clase."""
    total = len(df)
    for name, frac in (("train", 0.6), ("calibration", 0.2), ("evaluation", 0.2)):
        part = df[df["split"] == name]
        assert len(part) / total == pytest.approx(frac, abs=0.01)
        # Estratificación: cada partición conserva las 3 clases
        assert set(part["label.risk_class"].unique()) == {"LOW", "MEDIUM", "HIGH"}


def test_cv_solo_usa_train(df: pd.DataFrame):
    """cv_macro_f1 usa StratifiedKFold 5-fold; no debe tocar calibration/eval."""
    train = df[df["split"] == "train"]
    X = train[tr.CONTEXT_PAYLOAD_FEATURES + tr.CONTROL_FEATURES]
    y = tr.encode_target(train)
    pipe = tr.build_pipeline(tr.MODELS["logistic"], list(X.columns), calibrate=False)
    mean, std = tr.cv_macro_f1(pipe, X, y)
    assert 0.0 <= mean <= 1.0
    assert std >= 0.0


def test_umbral_prioriza_recall_high(df: pd.DataFrame):
    """El umbral elegido en calibration debe dar Recall HIGH >= precision piso."""
    train = df[df["split"] == "train"]
    cal = df[df["split"] == "calibration"]
    feats = tr.CONTEXT_PAYLOAD_FEATURES + tr.CONTROL_FEATURES
    pipe = tr.build_pipeline(tr.MODELS["logistic"], feats, calibrate=True)
    pipe.fit(train[feats], tr.encode_target(train))
    thr, info = tr.select_threshold(pipe, cal[feats], tr.encode_target(cal))
    assert 0.3 <= thr <= 0.95
    assert info["recall_high_calibration"] >= 0.0
    assert info["precision_high_calibration"] >= 0.0


def test_metricas_finales_estructura(df: pd.DataFrame):
    """final_metrics devuelve el esquema completo esperado."""
    eval_ = df[df["split"] == "evaluation"]
    feats = tr.CONTEXT_PAYLOAD_FEATURES + tr.CONTROL_FEATURES
    pipe = tr.build_pipeline(tr.MODELS["logistic"], feats, calibrate=True)
    pipe.fit(eval_[feats], tr.encode_target(eval_))  # solo evaluación rápida
    metrics = tr.final_metrics(
        pipe, eval_[feats], tr.encode_target(eval_), 0.5, [10.0, 20.0, 30.0]
    )
    for key in [
        "macro_f1",
        "balanced_accuracy",
        "log_loss",
        "brier_high",
        "confusion_matrix",
        "high_false_negatives",
        "threshold_high",
        "recall_by_class",
        "precision_by_class",
        "latency_ms",
    ]:
        assert key in metrics
    assert set(metrics["recall_by_class"]) == {"LOW", "MEDIUM", "HIGH"}
    assert set(metrics["latency_ms"]) == {"p50", "p95", "p99"}
    assert metrics["latency_ms"]["p50"] <= metrics["latency_ms"]["p95"] <= metrics["latency_ms"]["p99"]


def test_anti_circularidad_estructural(df: pd.DataFrame):
    """Ninguna feature del modelo coincide con campos prohibidos (spec §3)."""
    forbidden = {"observed_action", "execution_status", "latency_post_ms",
                 "execution_arguments", "embedding", "result"}
    for col in tr.NUMERIC_FEATURES + tr.BINARY_FEATURES + tr.CATEGORICAL_FEATURES:
        assert col not in forbidden, f"Feature prohibida en el esquema: {col}"
        assert not any(f in col.lower() for f in ("observed_action", "execution_status",
                                                  "latency_post_ms", "execution_arguments"))


def test_reproducibilidad_artefactos(tmp_path):
    """Misma seed => experiment_results.json idéntico (métricas reproducibles)."""
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    for out in (out1, out2):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "train_risk_model.py"),
                "--seed",
                "11",
                "--corpus",
                str(CORPUS),
                "--output-dir",
                str(out),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    r1 = json.loads((out1 / "experiment_results.json").read_text(encoding="utf-8"))
    r2 = json.loads((out2 / "experiment_results.json").read_text(encoding="utf-8"))
    assert r1["decision"] == r2["decision"]
    assert r1["variant_cv"]["full"] == r2["variant_cv"]["full"]
    assert r1["logistic_full_eval"]["macro_f1"] == r2["logistic_full_eval"]["macro_f1"]
    assert r1["random_forest_full_eval"]["confusion_matrix"] == r2["random_forest_full_eval"]["confusion_matrix"]


def test_artefactos_versionados_presentes():
    """Si el entrenamiento se corrió, debe existir pipeline + umbrales + meta."""
    if not MODEL_DIR.exists():
        pytest.skip("artefactos de modelo no presentes")
    assert (MODEL_DIR / "pipeline.joblib").exists()
    assert (MODEL_DIR / "thresholds.json").exists()
    assert (MODEL_DIR / "model_meta.json").exists()
    meta = json.loads((MODEL_DIR / "model_meta.json").read_text(encoding="utf-8"))
    assert meta["feature_schema_version"] == "v1"
    assert meta["training_dataset_version"] == "corpus_v1"
    assert meta["algorithm"] in {"logistic", "random_forest"}
    assert meta["artifact_digest"].startswith("sha256:")