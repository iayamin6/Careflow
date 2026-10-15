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
            for day in range(days):
                dt = start + timedelta(days=day)
                if dt < joined:
                    continue
                if rng.random() < 0.012:
                    quiet_until = day + int(rng.integers(20, 65))
                intensity = base * (0.015 if day < quiet_until else 1)
                if i % 11 == 0 and day > days - 50:
                    intensity *= 0.08
                if i % 17 == 0 and days - 70 < day < days - 15:
                    intensity *= 0.03
                for j in range(int(rng.poisson(intensity))):
                    time = dt + timedelta(seconds=int(rng.integers(1, 86399)))
                    if time >= end:
                        continue
                    mid = f"M{int(rng.integers(0, 12)):03}"
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
    return {
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
