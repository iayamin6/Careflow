import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from wallet import config, jobs, store


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = config.DB_PATH
        config.DB_PATH = Path(self.tmp.name) / "jobs.db"
        store.migrate()

    def tearDown(self):
        config.DB_PATH = self.old
        self.tmp.cleanup()

    def test_durable_dedup_and_atomic_claim(self):
        first = jobs.enqueue("spark")
        store.migrate()  # a new API/worker process performs the same migration
        self.assertEqual(jobs.enqueue("spark")["id"], first["id"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(jobs.claim, ["a", "b", "c", "d"]))
        self.assertEqual(sum(x is not None for x in claims), 1)
        self.assertEqual(jobs.listing()[0]["attempts"], 1)

    def test_expired_lease_requires_retry_and_rejects_stale_owner(self):
        job = jobs.enqueue("spark")
        jobs.claim("old")
        with store.connect() as c:
            c.execute(
                "UPDATE background_jobs SET heartbeat_at='2000-01-01T00:00:00+00:00'"
            )
        self.assertEqual(jobs.recover(), 1)
        self.assertIsNone(jobs.claim("new"))
        self.assertFalse(jobs.finish(job["id"], "old", "succeeded"))
        jobs.retry(job["id"])
        jobs.claim("new")
        self.assertFalse(jobs.heartbeat("old", job["id"]))
        self.assertFalse(jobs.finish(job["id"], "old", "succeeded"))
        self.assertTrue(jobs.finish(job["id"], "new", "succeeded", {"ok": True}))

    def test_retry_limit_and_active_conflict(self):
        job = jobs.enqueue("retrain")
        jobs.claim("worker")
        jobs.finish(job["id"], "worker", "failed")
        other = jobs.enqueue("retrain")
        with self.assertRaises(ValueError):
            jobs.retry(job["id"])
        jobs.claim("worker")
        jobs.finish(other["id"], "worker", "succeeded")
        for _ in range(2):
            jobs.retry(job["id"])
            jobs.claim("worker")
            jobs.finish(job["id"], "worker", "failed")
        with self.assertRaises(ValueError):
            jobs.retry(job["id"])

    def test_health_expires(self):
        self.assertFalse(jobs.worker_healthy())
        jobs.heartbeat("worker")
        self.assertTrue(jobs.worker_healthy())
        with store.connect() as c:
            c.execute("UPDATE workers SET heartbeat_at='2000-01-01T00:00:00+00:00'")
        self.assertFalse(jobs.worker_healthy())
