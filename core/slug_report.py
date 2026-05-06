from collections import defaultdict
from pathlib import Path
import csv


def _round_price(price):
    try:
        return round(float(price), 6)
    except Exception:
        return 0.0


def build_slug_timeline(rows: list[dict]) -> list[dict]:
    rows = sorted(rows, key=lambda x: (x.get("timestamp", ""), x.get("external_id", "")))
    out = []

    running_up_shares = 0.0
    running_down_shares = 0.0

    for r in rows:
        action = r.get("action_class", "OTHER")
        outcome = str(r.get("outcome", "")).lower()
        size = float(r.get("size", 0) or 0)
        price = float(r.get("price", 0) or 0)
        usd = float(r.get("amount_usd", 0) or 0)

        if action == "BUY":
            if "up" in outcome or "yes" in outcome:
                running_up_shares += size
            elif "down" in outcome or "no" in outcome:
                running_down_shares += size
        elif action == "MERGE":
            # merge сжигает пары, грубо уменьшаем обе стороны на размер merge
            merge_size = size
            running_up_shares = max(0.0, running_up_shares - merge_size)
            running_down_shares = max(0.0, running_down_shares - merge_size)

        out.append({
            "timestamp": r.get("timestamp", ""),
            "action_class": action,
            "outcome": r.get("outcome", ""),
            "price": price,
            "size": size,
            "amount_usd": usd,
            "running_up_shares": round(running_up_shares, 6),
            "running_down_shares": round(running_down_shares, 6),
            "market_slug": r.get("market_slug", ""),
            "event_slug": r.get("event_slug", ""),
        })

    return out


def build_slug_price_summary(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(lambda: {
        "count": 0,
        "shares": 0.0,
        "usd": 0.0,
    })

    for r in rows:
        action = r.get("action_class", "OTHER")
        outcome = str(r.get("outcome", "")).strip()
        price = _round_price(r.get("price", 0))
        size = float(r.get("size", 0) or 0)
        usd = float(r.get("amount_usd", 0) or 0)

        key = (action, outcome, price)
        grouped[key]["count"] += 1
        grouped[key]["shares"] += size
        grouped[key]["usd"] += usd

    out = []
    for (action, outcome, price), agg in grouped.items():
        out.append({
            "action_class": action,
            "outcome": outcome,
            "price": price,
            "count": agg["count"],
            "shares": round(agg["shares"], 6),
            "usd": round(agg["usd"], 6),
        })

    out.sort(key=lambda x: (x["action_class"], x["outcome"], x["price"]))
    return out


def print_slug_report(db, window_key: str, out_dir: str = "data/slug_reports"):
    rows = db.fetch_activity_for_window(window_key)

    if not rows:
        print(f"No rows found for window: {window_key}")
        return

    timeline = build_slug_timeline(rows)
    price_summary = build_slug_price_summary(rows)

    print("=" * 180)
    print(f"SLUG REPORT: {window_key}")
    print("=" * 180)
    print(f"events={len(rows)}")
    print()

    print("PRICE SUMMARY")
    print("-" * 120)
    for row in price_summary:
        print(
            f"{row['action_class']:<6} | "
            f"{row['outcome']:<10} | "
            f"price={row['price']:<10} | "
            f"count={row['count']:<6} | "
            f"shares={row['shares']:<12} | "
            f"usd={row['usd']:<12}"
        )

    print()
    print("TIMELINE (last 120 rows)")
    print("-" * 180)
    for row in timeline[-120:]:
        print(
            f"{row['timestamp']} | "
            f"{row['action_class']:<6} | "
            f"{row['outcome']:<10} | "
            f"price={row['price']:<10} | "
            f"size={row['size']:<10} | "
            f"usd={row['amount_usd']:<10} | "
            f"run_up={row['running_up_shares']:<12} | "
            f"run_down={row['running_down_shares']:<12}"
        )

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    safe_name = window_key.replace(" | ", "__").replace("/", "_").replace("\\", "_")

    timeline_csv = out_path / f"timeline__{safe_name}.csv"
    summary_csv = out_path / f"price_summary__{safe_name}.csv"

    with timeline_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(timeline[0].keys()))
        writer.writeheader()
        writer.writerows(timeline)

    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(price_summary[0].keys()))
        writer.writeheader()
        writer.writerows(price_summary)

    print()
    print(f"Saved timeline CSV: {timeline_csv}")
    print(f"Saved price summary CSV: {summary_csv}")