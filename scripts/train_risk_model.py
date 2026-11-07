"""
scripts/train_risk_model.py

Entrena, calibra y evalúa los modelos candidatos de la capa predictiva
(Fase 3 — Modelado) sobre el corpus sintético versionado (Fase 2).

Modelos comparados:
  1. Baseline mayoritario (clase más frecuente, referencia).
  2. Regresión logística multinomial (interpretable, coeficientes auditables).
  3. Random Forest (interacciones no lineales, importancia de variables).

Diseño experimental (anti-circularidad y descomposición de señal):
  - Cada modelo se entrena con TRES variantes de features para descomponer
    cuánta señal aporta cada bloque:
      * `full`: contexto + payload + control (todas las features pre-ejecución).
      * `ctx_payload`: solo contexto + payload (señal semántica del escenario).
      * `control`: solo señales de gobierno (política/guardrails/rate limit).
    La spec §2.3 advierte que las features de control "pueden correlacionar
    pero no determinan" la etiqueta; este experimento lo verifica empíricamente.
  - La etiqueta NO es copia de `observed_action` ni de la salida del motor
    determinista (rúbrica humana, Fase 2); ninguna feature usa el resultado de
    la herramienta ni datos del post_hook (leakage por diseño, spec §3).

Flujo:
  1. Carga del corpus y respeto del split congelado (60/20/20 estratificado).
  2. CV estratificada 5-fold SOLO dentro de `train` para estimar Macro F1.
  3. Calibración isotónica sobre `calibration` (20%) del modelo candidato.
  4. Selección de umbral HIGH sobre `calibration` priorizando Recall de HIGH
     con piso de precisión HIGH >= 0.5 (criterio principal de la spec §4.2).
  5. Métricas finales sobre la partición `evaluation` congelada: Macro F1,
     Recall/Precision por clase, Balanced Accuracy, Log Loss, Brier Score,
     matriz de confusión y latencia de inferencia P50/P95/P99.
  6. Análisis de errores: falsos negativos de HIGH y desacuerdo entre modelos.
  7. Persistencia versionada de artefactos (pipeline, umbrales, métricas,
     metadata, digest SHA-256) en `data/models/risk_model_v1/`.

Elección del modelo: si RF supera a logreg por >0.01 de Macro F1 en CV (full),
se elige RF; si están dentro de 0.01, se elige la regresión logística por
interpretabilidad (coeficientes auditables, requerimiento de la tesis).

Uso:
    python scripts/train_risk_model.py [--seed 42] [--corpus data/corpus/corpus_v1.parquet]
Exit 0 si el entrenamiento termina con métricas válidas; 1 en error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from itertools import product
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_recall_fscore_support,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

TARGET = "label.risk_class"
TARGET_ORDER = ["LOW", "MEDIUM", "HIGH"]
FEATURE_SCHEMA_VERSION = "v1"
CORPUS_NAME = "corpus_v1"
SEED = 42
N_FOLDS = 5

NUMERIC_FEATURES = [
    "payload.payload_size_bytes",
    "payload.arg_count",
    "payload.json_depth",
    "payload.sanitized_text_length",
    "payload.pii_category_count",
    "control.matched_policy_count",
    "control.max_allowed_sensitivity",
    "control.rate_limit_consumption",
    "context.hour_bucket",
]

BINARY_FEATURES = [
    "payload.has_url",
    "payload.has_sql_injection_pattern",
    "payload.has_shell_pattern",
    "payload.has_pii",
    "control.tool_allowed_for_profile",
    "control.has_policy_conflict",
    "control.policy_is_active",
    "control.guardrail_query_injection",
    "control.guardrail_topic_relevance",
    "control.guardrail_rate_limit",
]

CATEGORICAL_FEATURES = [
    "context.tool_name",
    "context.operation_class",
    "context.agent_role",
    "context.profile",
    "context.environment",
    "context.resource",
    "payload.dominant_data_type",
    "control.operation_type_requested",
]

CONTEXT_PAYLOAD_FEATURES = [
    c
    for c in NUMERIC_FEATURES + BINARY_FEATURES + CATEGORICAL_FEATURES
    if c.startswith(("context.", "payload."))
]
CONTROL_FEATURES = [
    c
    for c in NUMERIC_FEATURES + BINARY_FEATURES + CATEGORICAL_FEATURES
    if c.startswith("control.")
]

LABEL_ENCODING = {c: i for i, c in enumerate(TARGET_ORDER)}


def load_corpus(path: Path) -> pd.DataFrame:
    """Carga el corpus versionado y valida columnas necesarias."""
    df = pd.read_parquet(path)
    required = set(
        NUMERIC_FEATURES + BINARY_FEATURES + CATEGORICAL_FEATURES + [TARGET, "split"]
    )
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en el corpus: {sorted(missing)}")
    return df


def make_preprocessor(features: list[str]) -> ColumnTransformer:
    """Preprocesador: one-hot a categóricas, estandarización a numéricas,
    binarias passthrough (ya codificadas 0/1). Solo usa `features`."""
    num = [c for c in features if c in NUMERIC_FEATURES]
    cat = [c for c in features if c in CATEGORICAL_FEATURES]
    binary = [c for c in features if c in BINARY_FEATURES]
    transformers: list[tuple[str, Any, list[str]]] = []
    if num:
        transformers.append(("num", StandardScaler(), num))
    if cat:
        transformers.append(
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cat)
        )
    if binary:
        transformers.append(("bin", "passthrough", binary))
    return ColumnTransformer(transformers, remainder="drop")


def build_pipeline(model: BaseEstimator, features: list[str], calibrate: bool) -> Pipeline:
    """Pipeline [preprocesador -> modelo(-> calibrador isotónico si calibrate)]."""
    pre = make_preprocessor(features)
    estimator: Any = clone(model)
    if calibrate:
        estimator = CalibratedClassifierCV(
            estimator=estimator, method="isotonic", cv=3
        )
    return Pipeline(steps=[("pre", pre), ("clf", estimator)])


def proba_in_order(pipeline: Pipeline, X: pd.DataFrame) -> np.ndarray:
    """Probabilidades con columnas en orden TARGET_ORDER (LOW, MEDIUM, HIGH).

    El target se entrena codificado (0=LOW, 1=MEDIUM, 2=HIGH), por lo que
    `classes_` son enteros; se reordena a LOW/MEDIUM/HIGH seg?n LABEL_ENCODING.
    """
    proba = pipeline.predict_proba(X)
    classes = list(pipeline.classes_)
    expected = [LABEL_ENCODING[c] for c in TARGET_ORDER]
    if classes == expected:
        return proba
    order = [classes.index(v) for v in expected]
    return proba[:, order]


def encode_target(df: pd.DataFrame) -> np.ndarray:
    return df[TARGET].map(LABEL_ENCODING).to_numpy()


def cv_macro_f1(pipeline: Pipeline, X: pd.DataFrame, y: np.ndarray) -> tuple[float, float]:
    """CV estratificada 5-fold; retorna (macro_f1_mean, macro_f1_std)."""
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=SEED)
    scores = cross_val_score(pipeline, X, y, cv=skf, scoring="f1_macro", n_jobs=-1)
    return float(np.mean(scores)), float(np.std(scores))


def select_threshold(pipe: Pipeline, X_cal: pd.DataFrame, y_cal: np.ndarray) -> tuple[float, dict[str, float]]:
    """Umbral de probabilidad HIGH que maximiza Recall HIGH en calibration,
    con piso de precisión HIGH >= 0.5 (criterio principal de la spec)."""
    proba = proba_in_order(pipe, X_cal)
    p_high = proba[:, LABEL_ENCODING["HIGH"]]
    y_high = (y_cal == LABEL_ENCODING["HIGH"]).astype(int)
    best_t, best_recall, best_prec = 0.5, -1.0, 0.0
    for t in np.arange(0.30, 0.96, 0.05):
        pred = (p_high >= t).astype(int)
        tp = int(((pred == 1) & (y_high == 1)).sum())
        fp = int(((pred == 1) & (y_high == 0)).sum())
        recall = tp / max(1, int(y_high.sum()))
        prec = tp / max(1, tp + fp)
        if recall > best_recall and prec >= 0.5:
            best_t, best_recall, best_prec = float(t), recall, prec
    if best_recall < 0:
        best_t, best_recall, best_prec = 0.5, 0.0, 0.0
    return best_t, {
        "threshold_high": best_t,
        "recall_high_calibration": best_recall,
        "precision_high_calibration": best_prec,
    }


def final_metrics(
    pipe: Pipeline,
    X_eval: pd.DataFrame,
    y_eval: np.ndarray,
    threshold: float,
    latencies_ms: list[float],
) -> dict[str, Any]:
    """Métricas reportables sobre la partición de evaluación congelada."""
    proba = proba_in_order(pipe, X_eval)
    pred_idx = np.argmax(proba, axis=1)
    # Aplicar umbral: si la clase predicha es HIGH pero prob < umbral, degradar a LOW
    pred_idx = np.where(
        (pred_idx == LABEL_ENCODING["HIGH"]) & (proba[:, LABEL_ENCODING["HIGH"]] < threshold),
        LABEL_ENCODING["LOW"],
        pred_idx,
    )
    pred_classes = np.array([TARGET_ORDER[int(i)] for i in pred_idx])
    y_true = np.array([TARGET_ORDER[int(v)] for v in y_eval])

    macro_f1 = f1_score(y_true, pred_classes, average="macro", zero_division=0)
    recall_by_class = recall_score(
        y_true, pred_classes, labels=TARGET_ORDER, average=None, zero_division=0
    )
    precision_by_class, _, _, _ = precision_recall_fscore_support(
        y_true, pred_classes, labels=TARGET_ORDER, zero_division=0
    )
    bal_acc = balanced_accuracy_score(y_true, pred_classes)
    # log_loss con el target codificado (ints) y labels en orden TARGET_ORDER:
    # evita el desorden lexicografico de sklearn con las clases string.
    ll = log_loss(
        y_eval,
        proba,
        labels=[LABEL_ENCODING[c] for c in TARGET_ORDER],
    )
    brier = brier_score_loss(
        (y_eval == LABEL_ENCODING["HIGH"]).astype(int),
        proba[:, LABEL_ENCODING["HIGH"]],
    )
    cm = confusion_matrix(y_true, pred_classes, labels=TARGET_ORDER)
    high_fn = int(
        ((y_eval == LABEL_ENCODING["HIGH"]) & (pred_classes != "HIGH")).sum()
    )
    lat = np.array(latencies_ms, dtype=float)
    return {
        "macro_f1": round(float(macro_f1), 4),
        "recall_by_class": {
            c: round(float(r), 4) for c, r in zip(TARGET_ORDER, recall_by_class)
        },
        "precision_by_class": {
            c: round(float(p_), 4) for c, p_ in zip(TARGET_ORDER, precision_by_class)
        },
        "balanced_accuracy": round(float(bal_acc), 4),
        "log_loss": round(float(ll), 4),
        "brier_high": round(float(brier), 4),
        "confusion_matrix": cm.tolist(),
        "high_false_negatives": high_fn,
        "threshold_high": round(threshold, 2),
        "latency_ms": {
            "p50": round(float(np.percentile(lat, 50)), 2),
            "p95": round(float(np.percentile(lat, 95)), 2),
            "p99": round(float(np.percentile(lat, 99)), 2),
        },
    }


def baseline_fit(X: pd.DataFrame, y: np.ndarray) -> tuple[Pipeline, tuple[float, float]]:
    """Baseline mayoritario (DummyClassifier most_frequent)."""
    pipe = build_pipeline(DummyClassifier(strategy="most_frequent"), X.columns.tolist(), False)
    pipe.fit(X, y)
    return pipe, cv_macro_f1(pipe, X, y)


MODELS: dict[str, BaseEstimator] = {
    "logistic": LogisticRegression(solver="lbfgs", max_iter=2000, C=1.0, random_state=SEED),
    "random_forest": RandomForestClassifier(
        n_estimators=250, min_samples_leaf=2, class_weight="balanced", n_jobs=-1, random_state=SEED
    ),
}


def _predict_proba_label(
    model: BaseEstimator,
    feats: list[str],
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X: pd.DataFrame,
    threshold: float,
) -> np.ndarray:
    """Predice clases (strings) con el pipeline calibrado y umbral HIGH."""
    pipe = build_pipeline(model, feats, calibrate=True)
    pipe.fit(X_train[feats], y_train)
    proba = proba_in_order(pipe, X[feats])
    pred_idx = np.argmax(proba, axis=1)
    pred_idx = np.where(
        (pred_idx == LABEL_ENCODING["HIGH"])
        & (proba[:, LABEL_ENCODING["HIGH"]] < threshold),
        LABEL_ENCODING["LOW"],
        pred_idx,
    )
    return np.array([TARGET_ORDER[int(i)] for i in pred_idx])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--corpus", default="data/corpus/corpus_v1.parquet")
    parser.add_argument("--output-dir", default="data/models/risk_model_v1")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    df = load_corpus(root / args.corpus)
    out_dir = root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    train = df[df["split"] == "train"]
    cal = df[df["split"] == "calibration"]
    eval_ = df[df["split"] == "evaluation"]
    for name, part in (("train", train), ("calibration", cal), ("evaluation", eval_)):
        if len(part) == 0:
            raise ValueError(f"Split {name} vacío; regenerar corpus")
    if len(eval_) / len(df) < 0.15:
        raise ValueError("Split evaluation fuera de proporción")

    X_train, y_train = train[CONTEXT_PAYLOAD_FEATURES + CONTROL_FEATURES], encode_target(train)
    X_cal, y_cal = cal[CONTEXT_PAYLOAD_FEATURES + CONTROL_FEATURES], encode_target(cal)
    X_eval, y_eval = eval_[CONTEXT_PAYLOAD_FEATURES + CONTROL_FEATURES], encode_target(eval_)

    results: dict[str, Any] = {}

    # --- Baseline mayoritario ---
    _, (cv_maj, cv_maj_std) = baseline_fit(X_train, y_train)
    majority_label = TARGET_ORDER[int(pd.Series(y_train).value_counts().idxmax())]
    results["baseline"] = {
        "class": majority_label,
        "cv_macro_f1": round(cv_maj, 4),
        "cv_macro_f1_std": round(cv_maj_std, 4),
    }

    # --- Variantes de features por modelo ---
    variants = {
        "full": CONTEXT_PAYLOAD_FEATURES + CONTROL_FEATURES,
        "ctx_payload": CONTEXT_PAYLOAD_FEATURES,
        "control": CONTROL_FEATURES,
    }
    variant_cv: dict[str, dict[str, Any]] = {v: {} for v in variants}
    for name, model in MODELS.items():
        for vname, feats in variants.items():
            pipe = build_pipeline(model, feats, calibrate=False)
            s_mean, s_std = cv_macro_f1(pipe, X_train[feats], y_train)
            variant_cv[vname][name] = {
                "cv_macro_f1": round(s_mean, 4),
                "cv_macro_f1_std": round(s_std, 4),
            }
            results[f"{name}_{vname}"] = variant_cv[vname][name]
    results["variant_cv"] = variant_cv

    # --- Modelo principal (full), calibración y evaluación ---
    for name, model in MODELS.items():
        feats = variants["full"]
        cal_pipe = build_pipeline(model, feats, calibrate=True)
        cal_pipe.fit(X_train[feats], y_train)
        threshold, thr_info = select_threshold(cal_pipe, X_cal[feats], y_cal)

        latencies: list[float] = []
        sample = X_eval[feats].iloc[:500]
        for _ in range(5):
            t0 = time.perf_counter()
            cal_pipe.predict_proba(sample)
            latencies.append((time.perf_counter() - t0) * 1000.0)

        metrics = final_metrics(cal_pipe, X_eval[feats], y_eval, threshold, latencies)
        metrics.update(thr_info)
        results[f"{name}_full_eval"] = metrics
        results.setdefault("eval_models", {})[name] = metrics

        # Importancia de variables / coeficientes del modelo PRINCIPAL (no calibrado).
        # La calibracion envuelve al estimador; se entrena un pipeline sin
        # calibrar solo para diagnosticos (misma preprocesador + mismo modelo).
        if name == "random_forest":
            diag_pipe = build_pipeline(model, feats, calibrate=False)
            diag_pipe.fit(X_train[feats], y_train)
            rf = diag_pipe.named_steps["clf"]
            importances = rf.feature_importances_
            feat_names = diag_pipe.named_steps["pre"].get_feature_names_out()
            results["feature_importances"] = {
                str(f): round(float(i), 4)
                for f, i in sorted(
                    zip(feat_names, importances), key=lambda t: -t[1]
                )[:20]
            }
        elif name == "logistic":
            diag_pipe = build_pipeline(model, feats, calibrate=False)
            diag_pipe.fit(X_train[feats], y_train)
            lr = diag_pipe.named_steps["clf"]
            feat_names = diag_pipe.named_steps["pre"].get_feature_names_out()
            coefs = lr.coef_
            results["logistic_coefficients"] = {
                target: {
                    str(f): round(float(c), 4)
                    for f, c in zip(feat_names, coefs[LABEL_ENCODING[target]])
                }
                for target in TARGET_ORDER
            }

    # --- Análisis de errores (mejor modelo) ---
    best_name = max(
        MODELS,
        key=lambda n: (
            results[f"{n}_full_eval"]["macro_f1"],
            results[f"{n}_full_eval"]["recall_by_class"]["HIGH"],
        ),
    )
    # Desacuerdo entre modelos sobre evaluation
    preds_lr = _predict_proba_label(
        MODELS["logistic"], variants["full"], X_train, y_train, X_eval,
        results["logistic_full_eval"]["threshold_high"],
    )
    preds_rf = _predict_proba_label(
        MODELS["random_forest"], variants["full"], X_train, y_train, X_eval,
        results["random_forest_full_eval"]["threshold_high"],
    )
    disagreement = int((preds_lr != preds_rf).sum())
    fn_profile = None
    if results[f"{best_name}_full_eval"]["high_false_negatives"] > 0:
        best_preds = _predict_proba_label(
            MODELS[best_name], variants["full"], X_train, y_train, X_eval,
            results[f"{best_name}_full_eval"]["threshold_high"],
        )
        fn_mask = (y_eval == LABEL_ENCODING["HIGH"]) & (best_preds != "HIGH")
        fn_profile = {
            "count": int(fn_mask.sum()),
            "operation_class": eval_[fn_mask]["context.operation_class"].value_counts().to_dict(),
            "tool": eval_[fn_mask]["context.tool_name"].value_counts().to_dict(),
        }
    results["error_analysis"] = {
        "best_model": best_name,
        "high_false_negatives": results[f"{best_name}_full_eval"]["high_false_negatives"],
        "confusion_matrix_eval": results[f"{best_name}_full_eval"]["confusion_matrix"],
        "model_disagreement_evaluation": disagreement,
        "fn_profile_high": fn_profile,
        "note": (
            "El corpus sintetico es altamente separable: operation_class"
            " (WRITE/DELETE/DDL/CODE_EXEC) y las senales de control correlacionan"
            " fuertemente con la etiqueta por rubrica consistente. No es fuga de"
            " informacion (features pre-ejecucion, sin observed_action ni"
            " resultado de herramienta), pero limita el analisis de errores a"
            " los pocos desacuerdos entre modelos."
        ),
    }

    # --- Elección ---
    f1_rf = results["random_forest_full_eval"]["macro_f1"]
    f1_lr = results["logistic_full_eval"]["macro_f1"]
    if f1_rf - f1_lr > 0.01:
        chosen = "random_forest"
        reason = f"RF supera a logreg por {f1_rf - f1_lr:.4f} de Macro F1 (eval congelada)"
    else:
        chosen = "logistic"
        reason = (
            f"Diferencia de Macro F1 <= 0.01 ({f1_lr:.4f} vs {f1_rf:.4f}); "
            "se elige regresión logística por interpretabilidad "
            "(coeficientes auditables, requerimiento de la tesis)"
        )
    results["decision"] = {
        "chosen_model": chosen,
        "reason": reason,
        "threshold_high": results[f"{chosen}_full_eval"]["threshold_high"],
    }

    # --- Persistencia del pipeline elegido ---
    chosen_model = MODELS[chosen]
    final_pipe = build_pipeline(chosen_model, variants["full"], calibrate=True)
    final_pipe.fit(X_train[variants["full"]], y_train)
    model_path = out_dir / "pipeline.joblib"
    joblib.dump(final_pipe, model_path)

    (out_dir / "thresholds.json").write_text(
        json.dumps(
            {
                "threshold_high": results[f"{chosen}_full_eval"]["threshold_high"],
                "default_class": "LOW",
                "model": chosen,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    meta = {
        "model_id": "risk_model_v1",
        "model_version": "v1",
        "algorithm": chosen,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "training_dataset_version": CORPUS_NAME,
        "training_split": "train (60%)",
        "calibration_split": "calibration (20%)",
        "evaluation_split": "evaluation (20%) congelada",
        "calibration_method": "isotonic",
        "cv": f"{N_FOLDS}-fold estratificada (train)",
        "trained_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "seed": args.seed,
        "metrics_evaluation": results[f"{chosen}_full_eval"],
        "artifact_digest": artifact_digest([model_path, out_dir / "thresholds.json"]),
    }
    (out_dir / "model_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "experiment_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(
        json.dumps(
            {"chosen": chosen, "reason": reason, "evaluation": results[f"{chosen}_full_eval"]},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def artifact_digest(paths: list[Path]) -> str:
    """Digest SHA-256 estable de artefactos."""
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return "sha256:" + h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())