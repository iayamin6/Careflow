import json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from wallet import config, store, analytics, decisions, models


class WalletTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_PATH
        self.old_models = config.MODEL_DIR
        config.DB_PATH = Path(self.tmp.name) / "test.db"
        config.MODEL_DIR = Path(self.tmp.name) / "models"
        store.migrate()
        self.now = datetime.now(timezone.utc)
        self.n = 0
        self.customer("C1")
        self.merchant("M1")

    def tearDown(self):
        config.DB_PATH = self.old_db
        config.MODEL_DIR = self.old_models
        self.tmp.cleanup()

    def time(self, days):
        return store.utc((self.now - timedelta(days=days)).isoformat())

    def customer(self, id, consent=True, age=100):
        store.put_customer(
            {
                "id": id,
                "joined_at": self.time(age),
                "consent": consent,
                "synthetic": 1,
                "legacy_customer_id": None,
            }
        )

    def merchant(self, id):
        store.put_merchant(
            {
                "id": id,
                "joined_at": self.time(100),
                "category": "retail",
                "size": "small",
            }
        )

    def event(self, status="succeeded", days=1, cid="C1", mid="M1", **kwargs):
        self.n += 1
        data = {
            "event_id": f"E{self.n}",
            "transaction_id": f"T{self.n}",
            "attempt_id": f"A{self.n}",
            "customer_id": cid,
            "merchant_id": mid,
            "amount_minor": 1000,
            "currency": "BDT",
            "channel": "app",
            "status": status,
            "occurred_at": self.time(days),
            "latency_ms": 100,
            "failure_category": None,
        }
        data.update(kwargs)
        store.ingest(data)
        return data

    def test_duplicate_and_conflict(self):
        d = self.event()
        self.assertTrue(store.ingest(d)["duplicate"])
        with self.assertRaises(ValueError):
            store.ingest({**d, "amount_minor": 2000})
        with store.connect() as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0], 1
            )

    def test_late_success_preserves_timeout(self):
        d = self.event("timeout", 2)
        self.event(
            "succeeded",
            1,
            transaction_id=d["transaction_id"],
            attempt_id=d["attempt_id"],
        )
        self.event(
            "pending", 3, transaction_id=d["transaction_id"], attempt_id=d["attempt_id"]
        )
        s = analytics.snapshot()
        self.assertEqual(s["windows"]["7d"]["completion_rate"], 1)
        self.assertEqual(s["windows"]["7d"]["timeout_rate"], 1)
        self.assertEqual(s["customers"][0]["unresolved_transactions"], 0)
        self.assertFalse(
            any(a["kind"] == "payment_investigation" for a in decisions.recommend(s))
        )

    def test_retries_not_double_counted(self):
        d = self.event("failed")
        self.event("succeeded", 0.5, transaction_id=d["transaction_id"])
        m = analytics.snapshot()["windows"]["7d"]
        self.assertEqual(m["attempts"], 2)
        self.assertEqual(m["transactions"], 1)
        self.assertEqual(m["attempt_success_rate"], 0.5)
        self.assertEqual(m["completion_rate"], 1)

    def test_customer_payment_deterioration(self):
        for _ in range(5):
            self.event("failed")
        kinds = [
            a["kind"] for a in decisions.recommend(analytics.snapshot(), {"C1": 0.99})
        ]
        self.assertEqual(kinds, ["payment_investigation"])

    def test_merchant_incident_aggregates(self):
        for cid in ["C1", "C2", "C3"]:
            self.customer(cid)
            for _ in range(10):
                self.event(days=15, cid=cid)
            for _ in range(4):
                self.event("failed", cid=cid)
        result = decisions.refresh()
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["kind"], "merchant_investigation")
        self.assertEqual(len(decisions.refresh()["actions"]), 1)

    def test_decline_without_issues(self):
        for _ in range(20):
            self.event(days=40)
        for _ in range(3):
            self.event(days=2)
        self.assertIn(
            "retention_outreach",
            [a["kind"] for a in decisions.recommend(analytics.snapshot())],
        )

    def test_recovery_supersedes(self):
        result = decisions.refresh()
        self.assertEqual(result["actions"][0]["kind"], "re_engagement")
        for _ in range(10):
            self.event()
        actions = decisions.refresh()["actions"]
        self.assertEqual(actions[0]["status"], "superseded")

    def test_sparse_merchant(self):
        self.event("failed")
        m = analytics.snapshot()["merchants"][0]
        self.assertTrue(m["insufficient_data"])
        self.assertIsNone(m["health_score"])
        self.assertFalse(m["reliability_deterioration"])

    def test_approval_consent_and_duplicate_execution(self):
        a = decisions.refresh()["actions"][0]
        with self.assertRaises(ValueError):
            decisions.execute(a["id"], "key")
        decisions.approve(a["id"])
        self.customer("C1", False)
        with self.assertRaises(ValueError):
            decisions.execute(a["id"], "key")
        self.customer("C1", True)
        r = decisions.execute(a["id"], "key")
        self.assertEqual(r["status"], "succeeded")
        self.assertEqual(decisions.execute(a["id"], "key"), r)
        with self.assertRaises(ValueError):
            decisions.execute(a["id"], "different-key")

    def test_payment_issue_after_approval_blocks(self):
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        self.event("pending")
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            decisions.execute(a["id"], "key")

    def test_evidence_arriving_during_execution_blocks(self):
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        original = decisions.snapshot

        def racing_snapshot():
            result = original()
            self.event("pending")
            return result

        with patch.object(decisions, "snapshot", side_effect=racing_snapshot):
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                decisions.execute(a["id"], "racing-key")
        with store.connect() as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 0
            )

    def test_contact_limit_across_action_types(self):
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        decisions.execute(a["id"], "first-contact")
        with store.connect() as c:
            c.execute(
                "INSERT INTO actions(entity_type,entity_id,kind,priority,evidence,status,approval_required,approved,created_at,updated_at) VALUES('customer','C1','resolution_follow_up','normal','{}','approved',1,1,?,?)",
                (store.utc(), store.utc()),
            )
            followup = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "frequency limit"):
            decisions.execute(followup, "second-contact")

    def test_stale_pending_requires_investigation(self):
        self.event("pending", days=10)
        customer = analytics.snapshot()["customers"][0]
        self.assertTrue(customer["operational_problem"])
        self.assertEqual(
            decisions.refresh()["actions"][0]["kind"], "payment_investigation"
        )

    def test_failure_backoff_and_limit(self):
        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        r = decisions.execute(a["id"], "one", True)
        self.assertEqual(r["status"], "failed")
        with self.assertRaisesRegex(ValueError, "backoff"):
            decisions.execute(a["id"], "two", True)
        for key in ["two", "three"]:
            with store.connect() as c:
                c.execute(
                    "UPDATE actions SET next_retry_at=NULL WHERE id=?", (a["id"],)
                )
            decisions.execute(a["id"], key, True)
        self.assertEqual(decisions.list_actions()[0]["status"], "failed")
        with self.assertRaises(ValueError):
            decisions.execute(a["id"], "four", True)

    def test_missing_model_rules_work(self):
        s = analytics.snapshot()
        self.assertEqual(models.churn_predictions(s)["C1"]["status"], "unavailable")
        self.assertTrue(decisions.refresh()["actions"])

    def test_future_and_immutable_fields(self):
        with self.assertRaises(ValueError):
            self.event(days=-1)
        d = self.event()
        with self.assertRaises(ValueError):
            self.event(transaction_id=d["transaction_id"], amount_minor=5)
        with self.assertRaises(ValueError):
            self.event(attempt_id=d["attempt_id"])

    def test_receipt_time_prevents_lookahead(self):
        self.event(days=10)
        a, t, _, _ = analytics.frames(self.time(5))
        self.assertTrue(a.empty)

    def test_api_validation_and_views(self):
        from api.main import app

        client = TestClient(app)
        for path in [
            "/wallet",
            "/wallet/analytics",
            "/wallet/actions",
            "/wallet/models",
            "/wallet/automation",
            "/wallet/report",
        ]:
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(client.post("/wallet/events", json={}).status_code, 422)
        self.assertEqual(client.get("/wallet/customers/missing").status_code, 404)
        self.assertEqual(
            client.post(
                "/wallet/payment/predict",
                json={
                    "customer_id": "C1",
                    "merchant_id": "M1",
                    "amount_minor": 20,
                    "channel": "app",
                },
            ).json()["status"],
            "unavailable",
        )

    def test_redis_failure_fallback(self):
        from wallet.cache import cached_summary

        with patch.object(config, "REDIS_URL", "redis://localhost:1"):
            self.assertEqual(
                cached_summary(lambda: {"ok": True})["cache"],
                "unavailable; SQLite fallback",
            )

    def test_workflow_definitions(self):
        for f in Path("automation/wallet_workflows").glob("*.json"):
            d = json.loads(f.read_text())
            names = {n["name"] for n in d["nodes"]}
            self.assertFalse(d["active"])
            for source, groups in d["connections"].items():
                self.assertIn(source, names)
                for edges in groups["main"]:
                    for edge in edges:
                        self.assertIn(edge["node"], names)


if __name__ == "__main__":
    unittest.main()


class EdgeCases(unittest.TestCase):
    setUp = WalletTest.setUp
    tearDown = WalletTest.tearDown
    time = WalletTest.time
    customer = WalletTest.customer
    merchant = WalletTest.merchant
    event = WalletTest.event

    def test_old_pending_still_blocks_outreach(self):
        self.event("pending", days=40)
        self.assertEqual(
            analytics.snapshot()["customers"][0]["unresolved_transactions"], 1
        )
        self.assertFalse(
            any(
                a["approval_required"]
                for a in decisions.recommend(analytics.snapshot())
            )
        )

    def test_model_forecast_has_review_not_promotion(self):
        s = analytics.snapshot()
        actions = decisions.recommend(
            s, {"C1": 0.9}, {"C1": {"probability": 0.4, "notice": "hypothetical"}}
        )
        self.assertEqual(actions[0]["kind"], "payment_risk_review")
        self.assertFalse(actions[0]["approval_required"])

    def test_training_features_exclude_future(self):
        import pandas as pd

        self.event(days=10)
        events, cs = models.load_data()
        at = store.utc()
        features = models.churn_features(at, events, cs)
        future = events.iloc[0].copy()
        future["occurred_at"] = self.time(-2)
        future["received_at"] = self.time(-2)
        future["amount_minor"] = 999999
        changed = models.churn_features(
            at, pd.concat([events, pd.DataFrame([future])], ignore_index=True), cs
        )
        pd.testing.assert_frame_equal(features, changed)

    def test_all_six_workflow_exports(self):
        self.assertEqual(
            len(list(Path("automation/wallet_workflows").glob("*.json"))), 6
        )

    def test_resolution_followup_only_once(self):
        events = [self.event("timeout") for _ in range(4)]
        a = decisions.refresh()["actions"][0]
        decisions.execute(a["id"], "investigate")
        for e in events:
            self.event(
                "succeeded",
                0.5,
                transaction_id=e["transaction_id"],
                attempt_id=e["attempt_id"],
            )
        first = decisions.refresh()["actions"]
        self.assertEqual(sum(a["kind"] == "resolution_follow_up" for a in first), 1)
        self.event("succeeded")
        second = decisions.refresh()["actions"]
        self.assertEqual(sum(a["kind"] == "resolution_follow_up" for a in second), 1)

    def test_execution_key_cannot_cross_actions(self):
        self.customer("C2")
        actions = decisions.refresh()["actions"]
        for a in actions:
            decisions.approve(a["id"])
        decisions.execute(actions[0]["id"], "shared")
        with self.assertRaises(ValueError):
            decisions.execute(actions[1]["id"], "shared")

    def test_concurrent_duplicate_execution(self):
        from concurrent.futures import ThreadPoolExecutor

        a = decisions.refresh()["actions"][0]
        decisions.approve(a["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: decisions.execute(a["id"], "concurrent"), range(2))
            )
        self.assertEqual(results[0], results[1])
        with store.connect() as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 1
            )
