"""Reproducible synthetic wallet history; never represents production performance."""

import argparse, json
from datetime import datetime, timedelta, timezone
import numpy as np
from .store import connect, migrate, utc


def generate(seed=42, customers=120, days=360, as_of=None):
    migrate()
    rng = np.random.default_rng(seed)
    end = datetime.fromisoformat(utc(as_of)) if as_of else datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    records = []
    txs = []
    cid_rows = []
    with connect() as c:
        if c.execute("SELECT COUNT(*) FROM wallet_customers").fetchone()[0]:
            raise ValueError(
                "Demo database is not empty; select a new CAREFLOW_WALLET_DB to preserve history"
            )
        merchants = [
            (
                f"M{i:03}",
                ["grocery", "transport", "retail"][i % 3],
                utc(start.isoformat()),
                ["small", "medium", "large"][i % 3],
            )
            for i in range(12)
        ]
        c.executemany("INSERT INTO merchants VALUES(?,?,?,?)", merchants)
        for i in range(customers):
            cid = f"W{i:04}"
            joined = start + timedelta(
                days=days - 10 if i == customers - 1 else int(rng.integers(0, 35))
            )
            cid_rows.append((cid, utc(joined.isoformat()), int(i % 9 != 0), 1, None))
            base = rng.uniform(0.15, 1.5)
            quiet_until = -1
            friction = 0.0
            merchant_weights = np.array([1.0, 3.0, 6.0] * 4)
            merchant_weights /= merchant_weights.sum()
            for day in range(days):
                dt = start + timedelta(days=day)
                if dt < joined:
                    continue
                if rng.random() < 0.012:
                    quiet_until = day + int(rng.integers(20, 65))
                friction *= 0.94
                salary_cycle = 1.8 if dt.day <= 5 else 0.85
                monthly_user = (3.0 if dt.day <= 5 else 0.04) if i % 13 == 0 else 1.0
                weekend = 1.15 if dt.weekday() in (4, 5) else 1.0
                intensity = (
                    base
                    * salary_cycle
                    * monthly_user
                    * weekend
                    * max(0.08, 1 - friction)
                    * (0.015 if day < quiet_until else 1)
                )
                if i % 11 == 0 and day > days - 50:
                    intensity *= 0.08
                if i % 17 == 0 and days - 70 < day < days - 15:
                    intensity *= 0.03
                for j in range(int(rng.poisson(intensity))):
                    time = dt + timedelta(seconds=int(rng.integers(1, 86399)))
                    if time >= end:
                        continue
                    mid = f"M{int(rng.choice(12, p=merchant_weights)):03}"
                    amount = int(rng.lognormal(9, 1))
                    channel = ["app", "qr", "ussd"][int(rng.integers(0, 3))]
                    tid = f"T{i}-{day}-{j}"
                    txs.append(
                        (tid, cid, mid, amount, "BDT", channel, utc(time.isoformat()))
                    )
                    fail = (
                        0.035
                        + (0.13 if channel == "ussd" else 0)
                        + (0.12 if amount > 30000 else 0)
                        + (0.55 if mid == "M000" and day > days - 7 else 0)
                    )
                    status = (
                        "timeout"
                        if rng.random() < fail * 0.3
                        else "failed"
                        if rng.random() < fail
                        else "pending"
                        if rng.random() < 0.025
                        else "succeeded"
                    )
                    if status in ("failed", "timeout"):
                        friction = min(0.95, friction + rng.uniform(0.1, 0.45))
                    latency = int(rng.lognormal(6.1, 1))
                    event = (
                        f"E{tid}",
                        tid,
                        f"A{tid}",
                        status,
                        utc(time.isoformat()),
                        utc(time.isoformat()),
                        latency,
                        "network_timeout"
                        if status == "timeout"
                        else "provider_declined"
                        if status == "failed"
                        else None,
                        "{}",
                    )
                    records.append(event)
                    if status in ("timeout", "pending") and rng.random() < 0.6:
                        resolved = time + timedelta(minutes=int(rng.integers(1, 30)))
                        if resolved < end:
                            records.append(
                                (
                                    f"E{tid}-confirmed",
                                    tid,
                                    f"A{tid}",
                                    "succeeded",
                                    utc(resolved.isoformat()),
                                    utc(resolved.isoformat()),
                                    latency,
                                    None,
                                    "{}",
                                )
                            )
                    elif status == "failed" and rng.random() < 0.3:
                        retry = time + timedelta(minutes=2)
                        if retry < end:
                            records.append(
                                (
                                    f"E{tid}-retry",
                                    tid,
                                    f"A{tid}-2",
                                    "succeeded",
                                    utc(retry.isoformat()),
                                    utc(retry.isoformat()),
                                    latency,
                                    None,
                                    "{}",
                                )
                            )
                    elif status == "succeeded" and rng.random() < 0.018:
                        refund = time + timedelta(days=2)
                        if refund < end:
                            records.append(
                                (
                                    f"E{tid}-refund",
                                    tid,
                                    f"A{tid}",
                                    "refunded",
                                    utc(refund.isoformat()),
                                    utc(refund.isoformat()),
                                    latency,
                                    None,
                                    "{}",
                                )
                            )
        c.executemany("INSERT INTO wallet_customers VALUES(?,?,?,?,?)", cid_rows)
        c.executemany("INSERT INTO transactions VALUES(?,?,?,?,?,?,?)", txs)
        c.executemany("INSERT INTO payment_events VALUES(?,?,?,?,?,?,?,?,?)", records)
        successful = {e[1]: e for e in records if e[3] == "succeeded"}
        refunded = {e[1]: e for e in records if e[3] == "refunded"}
        for tx in txs:
            if tx[0] not in successful:
                continue
            due = datetime.fromisoformat(successful[tx[0]][4]) + timedelta(days=2)
            if due < end and rng.random() < 0.2:
                settled = (
                    None
                    if rng.random() < 0.05
                    else utc(min(end, due + timedelta(hours=2)).isoformat())
                )
                c.execute(
                    "INSERT INTO settlements VALUES(?,?,?,?,?,?)",
                    (
                        "S" + tx[0],
                        tx[0],
                        utc(due.isoformat()),
                        settled,
                        tx[3],
                        "settled" if settled else "pending",
                    ),
                )
            if tx[0] in refunded:
                e = refunded[tx[0]]
                c.execute(
                    "INSERT INTO refunds VALUES(?,?,?,?,?,?)",
                    (
                        "R" + tx[0],
                        tx[0],
                        tx[3],
                        "completed",
                        successful[tx[0]][4],
                        e[4],
                    ),
                )
        for i in range(min(customers, 12)):
            category = [
                "failed_payment",
                "delayed_transaction",
                "refund_problem",
                "merchant_payment",
                "account_access",
                "general_support",
            ][i % 6]
            c.execute(
                "INSERT INTO complaints(customer_id,text,category,sentiment,confidence,created_at) VALUES(?,?,?,?,?,?)",
                (
                    f"W{i:04}",
                    f"Synthetic support example: {category.replace('_', ' ')}",
                    category,
                    "negative",
                    0.6,
                    utc(end.isoformat()),
                ),
            )
    return {
        "generator_version": "wallet-v2-seasonal-friction",
        "seed": seed,
        "customers": customers,
        "merchants": 12,
        "transactions": len(txs),
        "events": len(records),
        "as_of": utc(end.isoformat()),
        "synthetic": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--customers", type=int, default=120)
    parser.add_argument("--days", type=int, default=360)
    parser.add_argument(
        "--as-of", default=None, help="Timezone-aware simulation cutoff"
    )
    args = parser.parse_args()
    print(json.dumps(generate(**vars(args)), indent=2))
