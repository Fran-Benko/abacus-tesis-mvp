"""Tests unitarios del monitoreo de deriva del modelo (hito prioridad media).

Cubre el núcleo matemático del PSI (Population Stability Index), la
clasificación de severidad y el baseline desde el corpus.
"""
from __future__ import annotations

import math

import pytest

from argentgob.analytics.drift import (
    PSI_MODERATE,
    PSI_NO_DRIFT,
    build_class_distribution,
    load_corpus_baseline,
    psi,
    psi_from_counts,
    severity,
)


def test_psi_identidad_es_cero():
    """Distribuciones iguales => PSI = 0 (sin deriva)."""
    dist = {"LOW": 0.5, "MEDIUM": 0.3, "HIGH": 0.2}
    assert psi(dist, dist) == pytest.approx(0.0, abs=1e-6)


def test_psi_distribucion_concentrada_es_significativa():
    """Baseline balanceado vs observado concentrado en HIGH => PSI alto."""
    reference = {"LOW": 0.5, "MEDIUM": 0.3, "HIGH": 0.2}
    candidate = {"LOW": 0.05, "MEDIUM": 0.05, "HIGH": 0.9}
    value = psi(candidate, reference)
    assert value > PSI_MODERATE


def test_psi_bins_vacios_no_divergen():
    """Bins ausentes se tratan como epsilon sin romper el calculo."""
    ref = {"LOW": 0.5, "MEDIUM": 0.3, "HIGH": 0.2}
    cand = {"LOW": 0.0, "MEDIUM": 0.0, "HIGH": 1.0}
    value = psi(cand, ref)
    assert math.isfinite(value)
    assert value >= 0.0


def test_psi_vacio_es_cero():
    assert psi({}, {"LOW": 1.0}) == 0.0
    assert psi({"LOW": 1.0}, {}) == 0.0


def test_psi_sin_masa_es_cero():
    """Distribucion observada sin ninguna muestra => 0 (no hay deriva medible)."""
    zeros = {"LOW": 0.0, "MEDIUM": 0.0, "HIGH": 0.0}
    ref = {"LOW": 0.5, "MEDIUM": 0.3, "HIGH": 0.2}
    assert psi(zeros, ref) == 0.0
    assert psi_from_counts({"LOW": 0, "MEDIUM": 0, "HIGH": 0}, {"LOW": 5, "MEDIUM": 3, "HIGH": 2}) == 0.0


def test_psi_from_counts_normaliza():
    """Conteos crudos se normalizan; identidad de conteos => 0."""
    counts = {"LOW": 5, "MEDIUM": 3, "HIGH": 2}
    assert psi_from_counts(counts, counts) == pytest.approx(0.0, abs=1e-6)
    # Contar el doble de muestras no cambia la distribucion.
    double = {"LOW": 10, "MEDIUM": 6, "HIGH": 4}
    assert psi_from_counts(counts, double) == pytest.approx(0.0, abs=1e-6)


def test_severity_umbrales():
    assert severity(PSI_NO_DRIFT - 0.01) == "sin_deriva"
    assert severity(PSI_NO_DRIFT + 0.01) == "moderada"
    assert severity(PSI_MODERATE - 0.01) == "moderada"
    assert severity(PSI_MODERATE + 0.01) == "significativa"


def test_build_class_distribution_desde_conteos():
    dist = build_class_distribution({"LOW": 1500, "MEDIUM": 900, "HIGH": 600})
    assert dist == {"LOW": 0.5, "MEDIUM": 0.3, "HIGH": 0.2}
    # Total = 1.0
    assert sum(dist.values()) == pytest.approx(1.0)
    # Total 0 -> ceros
    assert build_class_distribution({}) == {
        "LOW": 0.0,
        "MEDIUM": 0.0,
        "HIGH": 0.0,
    }


def test_load_corpus_baseline_lee_distribucion():
    """El baseline del corpus tiene la distribucion objetivo 50/30/20."""
    baseline = load_corpus_baseline()
    assert baseline["sample_size"] == 3000
    assert baseline["class_distribution"]["LOW"] == pytest.approx(0.5, abs=0.01)
    assert baseline["class_distribution"]["MEDIUM"] == pytest.approx(0.3, abs=0.01)
    assert baseline["class_distribution"]["HIGH"] == pytest.approx(0.2, abs=0.01)
    assert baseline["feature_psi_support"], "debe haber features comparables"
    assert baseline["model_id"] == "risk_model_v1"


def test_load_corpus_baseline_degrada_sin_corpus(tmp_path):
    """Sin parquet, degrada a baseline vacio sin excepcion."""
    baseline = load_corpus_baseline(str(tmp_path / "no_existe.parquet"))
    assert baseline["sample_size"] == 0
    assert baseline["feature_psi_support"] == []
    assert sum(baseline["class_distribution"].values()) == 0.0
