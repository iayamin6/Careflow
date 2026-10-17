import json
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_wallet
from wallet import (
    store,
    security,
    operations,
    decisions,
    delivery,
    experience,
    analytics,
    governance,
    batch,
)


class OperationsTest(unittest.TestCase):
    setUp = test_wallet.WalletTest.setUp
    tearDown = test_wallet.WalletTest.tearDown
    time = test_wallet.WalletTest.time
    customer = test_wallet.WalletTest.customer
    merchant = test_wallet.WalletTest.merchant
    event = test_wallet.WalletTest.event

    def client(self, role="admin"):
        from api.main import app

        return TestClient(
            app,
            headers={"Authorization": "Bearer " + security.issue("test-" + role, role)},
        )

    def test_auth_and_roles_protect_legacy_and_wallet(self):
        from api.main import app

        client = TestClient(app)
        self.assertEqual(client.get("/wallet/analytics").status_code, 401)
        self.assertEqual(client.get("/customers").status_code, 401)
        analyst = self.client("analyst")
        self.assertEqual(analyst.get("/wallet/analytics").status_code, 200)
        self.assertEqual(analyst.post("/wallet/decisions/refresh").status_code, 403)
        self.assertEqual(analyst.get("/wallet/operators").status_code, 403)
        automation = self.client("automation")
        self.assertEqual(automation.post("/wallet/actions/1/approve").status_code, 403)
        self.assertEqual(automation.post("/scores/run").status_code, 403)

    def test_token_revocation_expiry_and_audit_identity(self):
        client = self.client()
        operator = client.get("/wallet/session").json()
        client.post("/wallet/customers", json={"id": "C2", "joined_at": self.time(10)})
        with store.connect() as c:
            detail = json.loads(
                c.execute(
                    "SELECT detail FROM audit WHERE kind='customer_updated' ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
            )
        self.assertEqual(detail["actor"], "test-admin")
        self.assertEqual(
            client.post(f"/wallet/operators/{operator['id']}/revoke").status_code, 200
        )
        self.assertEqual(client.get("/wallet/session").status_code, 401)
        token = security.issue("expiry", "analyst")
        with store.connect() as c:
            c.execute(
                "UPDATE operators SET expires_at=? WHERE name='expiry'", (self.time(1),)
            )
        self.assertIsNone(security.identity(token))

    def test_rate_limit(self):
        from fastapi import FastAPI

        app = FastAPI()
        security.install(app)
        client = TestClient(app)
        for _ in range(180):
            client.get("/protected")
        self.assertEqual(client.get("/protected").status_code, 429)

    def test_override_suppresses_recreation(self):
        a = decisions.refresh()["actions"][0]
        operations.override(a["id"], "Customer requested no follow-up", 7)
        result = decisions.refresh()["actions"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "dismissed")

    def test_incident_closure_requires_evidence(self):
        event = self.event("pending", 2)
        decisions.refresh()
        incident = operations.incidents()[0]
        operations.update_incident(
            incident["id"], "investigating", "operator", "Checking provider"
        )
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            operations.update_incident(incident["id"], "resolved", "operator", "done")
        self.event(
            "succeeded",
            1,
            transaction_id=event["transaction_id"],
            attempt_id=event["attempt_id"],
        )
        operations.update_incident(
            incident["id"], "resolved", "operator", "Confirmation received"
        )
        self.assertEqual(operations.incidents()[0]["status"], "resolved")

    def test_marketing_preference_blocks_but_support_allowed(self):
        operations.preferences("C1", marketing=False, support=True)
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        with self.assertRaisesRegex(ValueError, "preference"):
            decisions.execute(a["id"], "blocked")

    def test_preference_api_cannot_verify_destination(self):
        client = self.client("support")
        self.assertEqual(
            client.post(
                "/wallet/customers/C1/preferences",
                json={
                    "channel": "email",
                    "destination": "demo@example.invalid",
                    "marketing": True,
                },
            ).status_code,
            200,
        )
        self.assertEqual(
            client.post("/wallet/operators/verify-destination/C1").status_code, 403
        )
        self.assertFalse(
            client.get("/wallet/customers/C1/preferences").json()["verified"]
        )

    def test_refunds_cannot_exceed_payment(self):
        e = self.event()
        operations.refund(
            {
                "id": "R1",
                "transaction_id": e["transaction_id"],
                "amount_minor": 700,
                "status": "requested",
            }
        )
        with self.assertRaisesRegex(ValueError, "exceeds"):
            operations.refund(
                {
                    "id": "R2",
                    "transaction_id": e["transaction_id"],
                    "amount_minor": 400,
                    "status": "requested",
                }
            )
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            operations.refund(
                {
                    "id": "R1",
                    "transaction_id": e["transaction_id"],
                    "amount_minor": 700,
                    "status": "completed",
                }
            )
        operations.refund(
            {
                "id": "R1",
                "transaction_id": e["transaction_id"],
                "amount_minor": 700,
                "status": "approved",
            }
        )
        operations.refund(
            {
                "id": "R1",
                "transaction_id": e["transaction_id"],
                "amount_minor": 700,
                "status": "completed",
            }
        )
        with self.assertRaises(ValueError):
            operations.refund(
                {
                    "id": "R1",
                    "transaction_id": e["transaction_id"],
                    "amount_minor": 700,
                    "status": "requested",
                }
            )

    def test_settlement_overdue_and_immutable(self):
        e = self.event(days=4)
        s = {
            "id": "S1",
            "transaction_id": e["transaction_id"],
            "amount_minor": 1000,
            "due_at": self.time(2),
            "settled_at": None,
        }
        operations.settlement(s)
        self.assertEqual(operations.servicing_report()["overdue_settlements"], 1)
        operations.settlement({**s, "settled_at": self.time(1)})
        self.assertEqual(operations.servicing_report()["overdue_settlements"], 0)
        with self.assertRaises(ValueError):
            operations.settlement(s)

    def test_complaints_influence_support_recommendations(self):
        for _ in range(6):
            self.event()
        r = experience.complaint("C1", "I am frustrated with a refund problem")
        self.assertEqual(r["category"], "refund_problem")
        actions = decisions.refresh()["actions"]
        self.assertEqual(actions[0]["kind"], "support_review")
        self.assertEqual(analytics.snapshot()["customers"][0]["open_complaints"], 1)

    def test_delivery_dry_run_and_late_optout(self):
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        decisions.execute(a["id"], "outbox")
        self.customer("C1", False)
        self.assertEqual(delivery.dispatch()[0]["status"], "blocked")
        self.assertEqual(delivery.dispatch(), [])

    def test_delivery_simulation_and_receipt_validation(self):
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        decisions.execute(a["id"], "outbox")
        self.assertEqual(delivery.dispatch()[0]["status"], "simulated")
        with self.assertRaises(ValueError):
            delivery.receipt("receipt", 1, "delivered")

    def test_provider_acceptance_not_claimed_delivered(self):
        operations.preferences("C1", "email", True, True, "test@example.invalid")
        operations.verify_destination("C1")
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        decisions.execute(a["id"], "external")
        with (
            patch.dict(os.environ, {"CAREFLOW_DELIVERY_MODE": "live"}),
            patch.object(delivery, "send", return_value=("accepted", "provider1")),
        ):
            self.assertEqual(delivery.dispatch()[0]["status"], "accepted")
        self.assertFalse(delivery.receipt("receipt", 1, "delivered")["duplicate"])
        self.assertTrue(delivery.receipt("receipt", 1, "delivered")["duplicate"])
        with self.assertRaises(ValueError):
            delivery.receipt("receipt", 1, "bounced")

    def test_ambiguous_delivery_is_not_retried(self):
        operations.preferences("C1", "email", True, True, "test@example.invalid")
        operations.verify_destination("C1")
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        decisions.execute(a["id"], "ambiguous")
        with (
            patch.dict(os.environ, {"CAREFLOW_DELIVERY_MODE": "live"}),
            patch.object(delivery, "send", side_effect=TimeoutError),
        ):
            self.assertEqual(delivery.dispatch()[0]["status"], "unknown")
            self.assertEqual(delivery.dispatch(), [])

    def test_experiment_holdout_and_unmatured_results(self):
        for i in range(2, 12):
            self.customer("C" + str(i))
        report = experience.create_experiment(
            "E1", "Retention test", ["C" + str(i) for i in range(1, 12)], 30
        )
        self.assertIsNone(report["retention_difference"])
        control = next(
            x["customer_id"] for x in report["assignments"] if x["arm"] == "control"
        )
        a = next(a for a in decisions.refresh()["actions"] if a["entity_id"] == control)
        decisions.approve(a["id"])
        with self.assertRaisesRegex(ValueError, "holdout"):
            decisions.execute(a["id"], "held-out")

    def test_empty_models_monitor_without_fabrication(self):
        report = governance.monitor()
        self.assertEqual(report["matured_rows"], 0)
        self.assertEqual(report["models"]["churn"]["status"], "unavailable")

    def test_new_pages_endpoints_and_validation(self):
        client = self.client()
        for path in [
            "incidents",
            "servicing",
            "complaints",
            "batch",
            "deliveries",
            "experiments",
            "models/candidates",
            "report",
        ]:
            self.assertEqual(client.get("/wallet/" + path).status_code, 200, path)
        self.assertEqual(
            client.post(
                "/wallet/refunds",
                json={"id": "R", "transaction_id": "T", "amount_minor": -1},
            ).status_code,
            422,
        )

    def test_batch_unavailable_is_explicit(self):
        self.assertEqual(batch.latest()["status"], "unavailable")
        with patch.dict(os.environ, {"SPARK_HOME": ""}):
            with self.assertRaises(ValueError):
                batch.run()

    def test_partial_and_full_refund_reconciliation(self):
        event = self.event()
        for id, amount in [("partial", 400), ("rest", 600)]:
            for status in ("requested", "approved", "completed", "completed"):
                operations.refund(
                    {
                        "id": id,
                        "transaction_id": event["transaction_id"],
                        "amount_minor": amount,
                        "status": status,
                    }
                )
            _, tx, _, _ = analytics.frames()
            self.assertEqual(
                tx.iloc[0].status, "succeeded" if id == "partial" else "refunded"
            )
        with store.connect() as c:
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM payment_events WHERE status='refunded'"
                ).fetchone()[0],
                1,
            )
        self.assertEqual(
            operations.servicing_report()["completed_refund_value_minor"], 1000
        )

    def test_open_complaint_blocks_marketing(self):
        experience.complaint("C1", "My refund is delayed")
        with store.connect() as c, self.assertRaisesRegex(ValueError, "complaint"):
            operations.communication_allowed(
                c, {"entity_id": "C1", "kind": "retention_outreach"}
            )

    def test_corrupt_model_pointer_is_unavailable(self):
        from wallet import models, config

        config.MODEL_DIR.mkdir(exist_ok=True)
        (config.MODEL_DIR / "active.json").write_text("invalid-json")
        self.assertIsNone(models.load("churn"))
        self.assertEqual(models.monitoring()["churn"]["status"], "unavailable")

    def test_candidate_promotion_gates_and_atomic_model_set(self):
        from wallet import config, models

        directory = config.MODEL_DIR / "candidates" / "candidate1"
        directory.mkdir(parents=True)
        (directory / "job.json").write_text(json.dumps({"status": "awaiting_review"}))
        for name in ("churn", "payment"):
            (directory / (name + ".json")).write_text(
                json.dumps({"test": {"pr_auc": 0.4}, "baseline_test": {"pr_auc": 0.2}})
            )
        with self.assertRaisesRegex(ValueError, "missing"):
            governance.promote("candidate1")
        self.assertFalse((config.MODEL_DIR / "active.json").exists())
        for name in ("churn", "payment"):
            (directory / (name + ".pkl")).write_bytes(b"test-artifact")
        self.assertEqual(governance.promote("candidate1")["status"], "promoted")
        self.assertEqual(models.active_directory(), directory)

    def test_delayed_receipt_training_inference_parity(self):
        from wallet import models

        first = self.event("failed", 2)
        with store.connect() as c:
            c.execute(
                "UPDATE payment_events SET received_at=? WHERE event_id=?",
                (self.time(1.5), first["event_id"]),
            )
        second = self.event("succeeded", 1)
        events, _ = models.load_data()
        dataset = models.payment_dataset(events)
        row = models.pre_attempt_row(
            events, "C1", "M1", 1000, "app", second["occurred_at"]
        )
        trained = dataset[dataset.as_of == second["occurred_at"]].iloc[0]
        for feature in models.PAYMENT_FEATURES:
            self.assertEqual(row[feature], trained[feature], feature)
        self.assertEqual(row["customer_prior_failure_rate"], 1)

    def test_dispatch_detects_new_evidence_during_check(self):
        original = analytics.snapshot

        def racing_snapshot():
            data = original()
            self.event("pending")
            return data

        with patch("wallet.analytics.snapshot", side_effect=racing_snapshot):
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                delivery.dispatch()

    def test_monthly_cadence_does_not_trigger_false_retention(self):
        with store.connect() as c:
            c.execute("UPDATE wallet_customers SET joined_at=?", (self.time(200),))
            c.execute("UPDATE merchants SET joined_at=?", (self.time(200),))
        for days in (35, 65, 95, 125):
            self.event("succeeded", days)
        data = analytics.snapshot()
        customer = data["customers"][0]
        self.assertEqual(customer["expected_inactivity_days"], 75)
        self.assertFalse(customer["inactivity_beyond_cadence"])
        self.assertFalse(
            any(
                a["kind"] in ("re_engagement", "retention_outreach")
                for a in decisions.recommend(data)
            )
        )

    def test_large_merchant_small_drop_has_absolute_impact(self):
        import pandas as pd
        from wallet.analytics import entity_metrics

        def frame(n, bad, days):
            return pd.DataFrame(
                [
                    {
                        "transaction_id": str(days) + "-" + str(i),
                        "attempt_id": str(days) + "-" + str(i),
                        "merchant_id": "M1",
                        "customer_id": "C1",
                        "status": "failed" if i < bad else "succeeded",
                        "started_at": self.time(days),
                        "latency_ms": 100,
                        "had_timeout": False,
                        "amount_minor": 1000,
                    }
                    for i in range(n)
                ]
            )

        rows = pd.concat([frame(1000, 50, 1), frame(4000, 40, 10)], ignore_index=True)
        result = entity_metrics(
            rows,
            rows,
            {
                "id": "M1",
                "joined_at": self.time(200),
                "category": "retail",
                "size": "large",
            },
            "merchant_id",
            store.utc(),
        )
        self.assertTrue(result["reliability_deterioration"])
        self.assertGreaterEqual(result["estimated_excess_failed_attempts"], 20)
