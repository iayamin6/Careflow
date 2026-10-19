"""Training is explicit. Inference never silently trains or invents predictions."""

import json, pickle
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from . import config
from .store import connect, utc

CHURN_FEATURES = [
    "frequency",
    "previous_frequency",
    "recency",
    "failure_rate",
    "amount_mean",
    "merchant_diversity",
    "tenure",
]
PAYMENT_FEATURES = [
    "amount_minor",
    "hour",
    "channel_ussd",
    "channel_qr",
    "customer_prior_failure_rate",
    "merchant_prior_failure_rate",
    "customer_prior_attempts",
]


def churn_features(at, events, customers):
    # Only information actually received by the observation cutoff is visible.
    known = events[(events.occurred_at <= at) & (events.received_at <= at)].copy()
    known["rank"] = known.status.map(
        {
            "pending": 0,
            "failed": 1,
            "timeout": 1,
            "succeeded": 2,
            "refunded": 3,
            "reversed": 3,
        }
    )
    known = known.sort_values(["rank", "occurred_at"]).drop_duplicates(
        "transaction_id", keep="last"
    )
    cutoff = pd.Timestamp(at)
    rows = []
    for c in customers.to_dict("records"):
        tenure = (cutoff - pd.Timestamp(c["joined_at"])).days
        if tenure < 30:
            continue
        t = known[known.customer_id == c["id"]]
        success = t[t.status == "succeeded"]
        recent = t[
            pd.to_datetime(t.occurred_at, utc=True) > cutoff - pd.Timedelta(days=30)
        ]
        s = success[
            pd.to_datetime(success.occurred_at, utc=True)
            > cutoff - pd.Timedelta(days=30)
        ]
        prior = success[
            (
                pd.to_datetime(success.occurred_at, utc=True)
                > cutoff - pd.Timedelta(days=60)
            )
            & (
                pd.to_datetime(success.occurred_at, utc=True)
                <= cutoff - pd.Timedelta(days=30)
            )
        ]
        rows.append(
            {
                "customer_id": c["id"],
                "as_of": at,
                "frequency": len(s),
                "previous_frequency": len(prior),
                "recency": min(
                    90,
                    (cutoff - pd.Timestamp(success.occurred_at.max())).total_seconds()
                    / 86400,
                )
                if len(success)
                else 90,
                "failure_rate": float(recent.status.isin(["failed", "timeout"]).mean())
                if len(recent)
                else 0,
                "amount_mean": float(s.amount_minor.mean()) if len(s) else 0,
                "merchant_diversity": s.merchant_id.nunique(),
                "tenure": tenure,
            }
        )
    return pd.DataFrame(rows)


def load_data():
    with connect() as c:
        events = pd.read_sql_query(
            "SELECT e.*,t.customer_id,t.merchant_id,t.amount_minor,t.channel FROM payment_events e JOIN transactions t ON t.id=e.transaction_id",
            c,
        )
        customers = pd.read_sql_query("SELECT * FROM wallet_customers", c)
    return events, customers


def terminal_history(events):
    """First terminal outcomes become usable only when actually received."""
    first = events.sort_values(["occurred_at", "event_id"]).drop_duplicates(
        "attempt_id"
    )
    terminal = (
        events[events.status.isin(["failed", "timeout", "succeeded"])]
        .sort_values(["occurred_at", "event_id"])
        .drop_duplicates("attempt_id")
        .copy()
    )
    terminal["attempt_started_at"] = terminal.attempt_id.map(
        first.set_index("attempt_id").occurred_at
    )
    terminal["available_at"] = terminal[["occurred_at", "received_at"]].max(axis=1)
    return first, terminal


def pre_attempt_row(
    events, customer_id, merchant_id, amount_minor, channel, at, terminal=None
):
    if terminal is None:
        _, terminal = terminal_history(events)
    cutoff = utc((datetime.fromisoformat(at) - timedelta(days=7)).isoformat())
    history = terminal[
        (terminal.available_at < at)
        & (terminal.attempt_started_at > cutoff)
        & (terminal.attempt_started_at < at)
    ]
    customer = history[history.customer_id == customer_id]
    merchant = history[history.merchant_id == merchant_id]
    return {
        "amount_minor": float(amount_minor),
        "hour": datetime.fromisoformat(at).hour,
        "channel_ussd": int(channel == "ussd"),
        "channel_qr": int(channel == "qr"),
        "customer_prior_attempts": len(customer),
        "customer_prior_failure_rate": float(
            customer.status.isin(["failed", "timeout"]).mean()
        )
        if len(customer)
        else 0,
        "merchant_prior_failure_rate": float(
            merchant.status.isin(["failed", "timeout"]).mean()
        )
        if len(merchant)
        else 0,
    }


def payment_dataset(events):
    from collections import defaultdict, deque

    first, terminal = terminal_history(events)
    outcomes = terminal.set_index("attempt_id").status.to_dict()
    known_times = terminal.set_index("attempt_id").available_at.to_dict()
    timeline = terminal.sort_values("available_at").to_dict("records")
    histories = defaultdict(deque)
    cursor = 0
    rows = []
    for r in first.sort_values("occurred_at").to_dict("records"):
        time = pd.Timestamp(r["occurred_at"])
        while (
            cursor < len(timeline)
            and timeline[cursor]["available_at"] < r["occurred_at"]
        ):
            item = timeline[cursor]
            cursor += 1
            entry = (
                pd.Timestamp(item["available_at"]),
                int(item["status"] in ("failed", "timeout")),
                pd.Timestamp(item["attempt_started_at"]),
            )
            for key, label in [
                (item["customer_id"], "customer"),
                (item["merchant_id"], "merchant"),
            ]:
                histories[(label, key)].append(entry)
        values = {}
        for key, label in [
            (r["customer_id"], "customer"),
            (r["merchant_id"], "merchant"),
        ]:
            history = histories[(label, key)]
            while history and history[0][0] <= time - pd.Timedelta(days=7):
                history.popleft()
            visible = [x for x in history if time - pd.Timedelta(days=7) < x[2] < time]
            values[label + "_prior_failure_rate"] = (
                sum(x[1] for x in visible) / len(visible) if visible else 0
            )
            if label == "customer":
                values["customer_prior_attempts"] = len(visible)
        outcome = outcomes.get(r["attempt_id"])
        if outcome:
            rows.append(
                {
                    "as_of": r["occurred_at"],
                    "label_known_at": known_times[r["attempt_id"]],
                    "target": int(outcome in ("failed", "timeout")),
                    "amount_minor": r["amount_minor"],
                    "hour": time.hour,
                    "channel_ussd": int(r["channel"] == "ussd"),
                    "channel_qr": int(r["channel"] == "qr"),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def evaluate(y, p):
    from sklearn.metrics import (
        average_precision_score,
        precision_score,
        recall_score,
        brier_score_loss,
        roc_auc_score,
    )

    return {
        "pr_auc": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
        "precision_at_0_5": float(precision_score(y, p >= 0.5, zero_division=0)),
        "recall_at_0_5": float(recall_score(y, p >= 0.5, zero_division=0)),
        "brier": float(brier_score_loss(y, p)),
        "rows": int(len(y)),
        "positive_rate": float(np.mean(y)),
    }


def train():
    from xgboost import XGBClassifier
    from sklearn.linear_model import LogisticRegression

    events, customers = load_data()
    if events.empty:
        raise ValueError("Generate wallet history first")
    end = pd.Timestamp(events.occurred_at.max())
    start = pd.Timestamp(events.occurred_at.min())
    if (end - start).days < 330:
        raise ValueError(
            "At least 330 days of history are required for temporal training/calibration/test"
        )
    samples = []
    for age in [60, 90, 120, 150, 180, 210, 240, 300]:
        at = utc((start + pd.Timedelta(days=age)).isoformat())
        df = churn_features(at, events, customers)
        future = events[
            (events.occurred_at > at)
            & (
                events.occurred_at
                <= utc((pd.Timestamp(at) + pd.Timedelta(days=30)).isoformat())
            )
            & (events.status == "succeeded")
        ]
        df["target"] = (~df.customer_id.isin(future.customer_id)).astype(int)
        df["age"] = age
        samples.append(df)
    churn = pd.concat(samples, ignore_index=True)
    payment = payment_dataset(events)
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    reports = {}
    for name, df, features, masks in [
        (
            "churn",
            churn,
            CHURN_FEATURES,
            [churn.age <= 180, churn.age == 210, churn.age == 240, churn.age == 300],
        ),
        (
            "payment",
            payment,
            PAYMENT_FEATURES,
            [
                pd.to_datetime(payment.as_of, utc=True)
                < start + pd.Timedelta(days=180),
                (
                    pd.to_datetime(payment.as_of, utc=True)
                    >= start + pd.Timedelta(days=210)
                )
                & (
                    pd.to_datetime(payment.as_of, utc=True)
                    < start + pd.Timedelta(days=240)
                ),
                (
                    pd.to_datetime(payment.as_of, utc=True)
                    >= start + pd.Timedelta(days=240)
                )
                & (
                    pd.to_datetime(payment.as_of, utc=True)
                    < start + pd.Timedelta(days=270)
                ),
                pd.to_datetime(payment.as_of, utc=True)
                >= start + pd.Timedelta(days=300),
            ],
        ),
    ]:
        tr, va, ca, te = [df[m].copy() for m in masks]
        if name == "payment":
            tr = tr[
                tr.label_known_at < utc((start + pd.Timedelta(days=210)).isoformat())
            ]
            va = va[
                va.label_known_at < utc((start + pd.Timedelta(days=240)).isoformat())
            ]
            ca = ca[
                ca.label_known_at < utc((start + pd.Timedelta(days=300)).isoformat())
            ]
            te = te[te.label_known_at <= utc(end.isoformat())]
        if any(len(x) == 0 or x.target.nunique() < 2 for x in (tr, va, ca, te)):
            raise ValueError(
                f"{name}: each temporal split requires both target classes"
            )
        candidates = {
            "xgboost": XGBClassifier(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.06,
                subsample=0.9,
                random_state=42,
                n_jobs=2,
                eval_metric="logloss",
            )
        }
        try:
            from lightgbm import LGBMClassifier

            candidates["lightgbm"] = LGBMClassifier(
                n_estimators=100,
                max_depth=3,
                num_leaves=7,
                verbosity=-1,
                n_jobs=2,
                random_state=42,
            )
        except ImportError:
            pass
        fitted = {}
        selection = {}
        for algo, model in candidates.items():
            model.fit(tr[features], tr.target)
            p = model.predict_proba(va[features])[:, 1]
            selection[algo] = evaluate(va.target, p)
            fitted[algo] = model
        winner = max(selection, key=lambda x: selection[x]["pr_auc"])
        model = fitted[winner]

        # Sigmoid calibration is learned only on the middle time window.
        def logit(p):
            return np.log(
                np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6))
            ).reshape(-1, 1)

        calibration = LogisticRegression().fit(
            logit(model.predict_proba(ca[features])[:, 1]), ca.target
        )
        raw = model.predict_proba(te[features])[:, 1]
        prob = calibration.predict_proba(logit(raw))[:, 1]
        from sklearn.metrics import precision_recall_curve

        calibration_prob = calibration.predict_proba(
            logit(model.predict_proba(ca[features])[:, 1])
        )[:, 1]
        precision, recall, thresholds = precision_recall_curve(
            ca.target, calibration_prob
        )
        f1 = (
            2
            * precision[:-1]
            * recall[:-1]
            / np.maximum(precision[:-1] + recall[:-1], 1e-12)
        )
        threshold = float(thresholds[int(np.argmax(f1))]) if len(thresholds) else 0.5
        from sklearn.metrics import precision_score, recall_score

        report = {
            "name": name,
            "version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "algorithm": winner,
            "synthetic_only": True,
            "target": "No successful transaction in the next 30 days"
            if name == "churn"
            else "First terminal attempt outcome is failed or timeout",
            "features": features,
            "selection_validation": selection,
            "test": evaluate(te.target, prob),
            "review_threshold": threshold,
            "test_at_review_threshold": {
                "precision": float(
                    precision_score(te.target, prob >= threshold, zero_division=0)
                ),
                "recall": float(
                    recall_score(te.target, prob >= threshold, zero_division=0)
                ),
            },
            "threshold_method": "Maximum F1 on calibration data; internal review policy only, not permission to contact customers",
            "baseline_test": evaluate(te.target, np.full(len(te), tr.target.mean())),
            "splits": {
                label: {"rows": len(x), "first": x.as_of.min(), "last": x.as_of.max()}
                for label, x in [
                    ("train", tr),
                    ("selection", va),
                    ("calibration", ca),
                    ("test", te),
                ]
            },
            "feature_reference": {
                f: {"mean": float(tr[f].mean()), "std": float(tr[f].std())}
                for f in features
            },
            "calibration": "Separate chronological selection and calibration periods; final test untouched",
            "last_refresh": utc(),
            "limitations": [
                "Synthetic patterns only; no production validity",
                "Same customer cohorts may appear across temporal snapshots",
                "SHAP explains raw model margin, not calibrated probability",
            ],
            "tracking": "local metadata",
        }
        artifact = {
            "model": model,
            "calibrator": calibration,
            "features": features,
            "report": report,
        }
        with open(config.MODEL_DIR / f"{name}.pkl", "wb") as f:
            pickle.dump(artifact, f)
        try:
            import mlflow

            mlflow.set_tracking_uri(config.MLFLOW_URI)
            mlflow.set_experiment("careflow-wallet-synthetic")
            with mlflow.start_run(run_name=name + "-" + report["version"]) as run:
                mlflow.log_params(
                    {
                        "model": name,
                        "algorithm": winner,
                        "synthetic": True,
                        "features": ",".join(features),
                    }
                )
                mlflow.log_metrics(
                    {
                        k: v
                        for k, v in report["test"].items()
                        if isinstance(v, (int, float))
                    }
                )
                mlflow.log_dict(report, "evaluation.json")
                mlflow.log_artifact(str(config.MODEL_DIR / f"{name}.pkl"))
                report["mlflow_run_id"] = run.info.run_id
                report["tracking"] = "mlflow"
        except Exception as e:
            report["tracking_error"] = type(e).__name__ + ": " + str(e)[:200]
        (config.MODEL_DIR / f"{name}.json").write_text(json.dumps(report, indent=2))
        reports[name] = report
    return reports


def active_directory():
    pointer = config.MODEL_DIR / "active.json"
    if pointer.exists():
        candidate = json.loads(pointer.read_text())["candidate"]
        if not candidate.isalnum():
            raise ValueError("Invalid active model identifier")
        return config.MODEL_DIR / "candidates" / candidate
    return config.MODEL_DIR


def load(name):
    try:
        path = active_directory() / f"{name}.pkl"
        if not path.exists():
            return None
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def prediction(artifact, row):
    x = pd.DataFrame([row])[artifact["features"]]
    raw = float(artifact["model"].predict_proba(x)[0, 1])
    z = np.log(np.clip(raw, 1e-6, 1 - 1e-6) / (1 - np.clip(raw, 1e-6, 1 - 1e-6)))
    p = float(artifact["calibrator"].predict_proba([[z]])[0, 1])
    result = {
        "status": "available",
        "probability": p,
        "raw_probability": raw,
        "version": artifact["report"]["version"],
        "drivers": [],
        "explanation_scope": "SHAP contributions to raw model margin; calibrated probability is a separate sigmoid transformation",
    }
    try:
        import shap

        explanation = shap.TreeExplainer(artifact["model"])(x)
        values = np.asarray(explanation.values).reshape(-1)
        result["drivers"] = [
            {
                "feature": f,
                "value": float(x.iloc[0][f]),
                "contribution": float(v),
                "direction": "increases raw risk" if v > 0 else "decreases raw risk",
            }
            for f, v in sorted(
                zip(artifact["features"], values), key=lambda z: abs(z[1]), reverse=True
            )[:4]
        ]
    except Exception as e:
        result["explanation_error"] = type(e).__name__
    return result


def churn_predictions(data):
    artifact = load("churn")
    if artifact is None:
        return {
            c["id"]: {"status": "unavailable", "probability": None}
            for c in data["customers"]
        }
    events, customers = load_data()
    features = churn_features(data["as_of"], events, customers)
    rows = (
        features[
            features.customer_id.isin([c["id"] for c in data["customers"]])
        ].to_dict("records")
        if len(features)
        else []
    )
    return {r["customer_id"]: prediction(artifact, r) for r in rows}


def monitoring():
    result = {}
    for name in ("payment", "churn"):
        try:
            path = active_directory() / f"{name}.json"
            result[name] = (
                json.loads(path.read_text())
                if path.exists()
                else {"status": "unavailable"}
            )
        except (ValueError, KeyError, OSError):
            result[name] = {
                "status": "unavailable",
                "reason": "Invalid or unreadable model metadata",
            }
    if result["churn"].get("features"):
        events, customers = load_data()
        df = churn_features(utc(), events, customers)
        result["churn"]["feature_drift"] = (
            {
                f: round(abs(float(df[f].mean()) - r["mean"]) / max(r["std"], 1e-6), 3)
                for f, r in result["churn"]["feature_reference"].items()
            }
            if len(df)
            else {}
        )
        result["churn"]["drift_definition"] = (
            "Absolute standardized mean shift from training; investigate values above 2, not proof of model failure"
        )
    return result


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))


def payment_risks(data):
    """Next-attempt scenario using last observed merchant/channel and median amount, not a claim about an actual future attempt."""
    artifact = load("payment")
    if artifact is None:
        return {}
    events, _ = load_data()
    if events.empty:
        return {}
    at = data["as_of"]
    known = events[(events.occurred_at < at) & (events.received_at < at)]
    first, terminal = terminal_history(known)
    result = {}
    for customer in data["customers"]:
        history = first[first.customer_id == customer["id"]].sort_values("occurred_at")
        if history.empty:
            continue
        last = history.iloc[-1]
        row = pre_attempt_row(
            known,
            customer["id"],
            last.merchant_id,
            history.amount_minor.median(),
            last.channel,
            at,
            terminal=terminal,
        )
        result[customer["id"]] = {
            **prediction(artifact, row),
            "scenario": {
                "merchant_id": last.merchant_id,
                "channel": last.channel,
                "amount_minor": row["amount_minor"],
            },
            "notice": "Hypothetical next attempt with last merchant/channel and median historical amount",
        }
    return result
