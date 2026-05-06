import csv
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.utils import parse_dt, safe_float


def _kyiv_tz():
    try:
        return ZoneInfo("Europe/Kyiv")
    except ZoneInfoNotFoundError:
        # April dates are DST in Kyiv (UTC+3). Use +3 as fallback.
        return timezone(timedelta(hours=3))


def _kyiv_day(ts_iso: str) -> str:
    dt = parse_dt(ts_iso)
    if dt is None:
        return ""
    return dt.astimezone(_kyiv_tz()).strftime("%Y-%m-%d")


def _duration_bucket(slug: str) -> str:
    text = (slug or "").lower()
    if "-5m-" in text:
        return "5m"
    if "-15m-" in text:
        return "15m"
    if "-1h-" in text:
        return "1h"
    return "other"


def _asset_from_slug(slug: str) -> str:
    text = (slug or "").strip().lower()
    if not text:
        return "other"
    return text.split("-")[0]


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator * 100.0


def write_buy_sell_strategy_profile(
    db,
    settings,
    out_dir: str,
    date_kyiv: str | None = None,
) -> tuple[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not date_kyiv:
        date_kyiv = datetime.now(_kyiv_tz()).strftime("%Y-%m-%d")

    txt_path = out / f"strategy_profile_{date_kyiv}.txt"
    csv_path = out / f"strategy_profile_{date_kyiv}.csv"

    activity_rows = db.fetch_all_activity()
    closed_rows = db.fetch_latest_closed_positions(limit=100000)

    day_activity = [r for r in activity_rows if _kyiv_day(r.get("timestamp", "")) == date_kyiv]
    day_closed = [r for r in closed_rows if _kyiv_day(r.get("snapshot_time", "")) == date_kyiv]

    action_counts = Counter()
    buy_amounts = []
    buy_sizes = []
    buy_outcomes = Counter()
    buy_asset_duration = defaultdict(lambda: {"count": 0, "usd": 0.0, "shares": 0.0})

    for r in day_activity:
        action = (r.get("action_class") or "").upper()
        action_counts[action] += 1
        if action != "BUY":
            continue
        amount = safe_float(r.get("amount_usd"))
        size = safe_float(r.get("size"))
        buy_amounts.append(amount)
        buy_sizes.append(size)

        outcome = (r.get("outcome") or "").strip() or "N/A"
        buy_outcomes[outcome] += 1

        slug = r.get("market_slug") or r.get("event_slug") or ""
        key = (_asset_from_slug(slug), _duration_bucket(slug))
        d = buy_asset_duration[key]
        d["count"] += 1
        d["usd"] += amount
        d["shares"] += size

    closed_total_bought = 0.0
    closed_total_pnl = 0.0
    closed_wins = 0
    closed_losses = 0
    closed_flats = 0

    by_duration = defaultdict(lambda: {"rows": 0, "bought": 0.0, "pnl": 0.0})
    by_asset = defaultdict(lambda: {"rows": 0, "bought": 0.0, "pnl": 0.0})
    by_outcome = defaultdict(lambda: {"rows": 0, "bought": 0.0, "pnl": 0.0})

    for r in day_closed:
        bought = safe_float(r.get("total_bought"))
        pnl = safe_float(r.get("realized_pnl"))
        closed_total_bought += bought
        closed_total_pnl += pnl

        if pnl > 0:
            closed_wins += 1
        elif pnl < 0:
            closed_losses += 1
        else:
            closed_flats += 1

        slug = r.get("market_slug") or r.get("event_slug") or ""
        duration = _duration_bucket(slug)
        asset = _asset_from_slug(slug)
        outcome = (r.get("outcome") or "").strip() or "N/A"

        for bucket_key, key in (("duration", duration), ("asset", asset), ("outcome", outcome)):
            if bucket_key == "duration":
                d = by_duration[key]
            elif bucket_key == "asset":
                d = by_asset[key]
            else:
                d = by_outcome[key]
            d["rows"] += 1
            d["bought"] += bought
            d["pnl"] += pnl

    buy_amount_avg = statistics.mean(buy_amounts) if buy_amounts else 0.0
    buy_amount_median = statistics.median(buy_amounts) if buy_amounts else 0.0
    buy_size_avg = statistics.mean(buy_sizes) if buy_sizes else 0.0
    buy_size_median = statistics.median(buy_sizes) if buy_sizes else 0.0

    closed_total = len(day_closed)
    closed_winrate = _pct(closed_wins, closed_total)
    closed_roi = _pct(closed_total_pnl, closed_total_bought)

    csv_rows = []
    csv_rows.append(
        {
            "section": "summary",
            "key": "totals",
            "rows": closed_total,
            "count": len(day_activity),
            "amount_usd": round(closed_total_bought, 6),
            "pnl_usd": round(closed_total_pnl, 6),
            "roi_pct": round(closed_roi, 6),
            "extra": f"winrate={round(closed_winrate, 4)}; wins={closed_wins}; losses={closed_losses}; flats={closed_flats}",
        }
    )

    for action, c in sorted(action_counts.items(), key=lambda x: x[1], reverse=True):
        csv_rows.append(
            {
                "section": "activity_action_counts",
                "key": action,
                "rows": "",
                "count": c,
                "amount_usd": "",
                "pnl_usd": "",
                "roi_pct": "",
                "extra": "",
            }
        )

    for (asset, duration), d in sorted(buy_asset_duration.items(), key=lambda x: x[1]["usd"], reverse=True):
        csv_rows.append(
            {
                "section": "buy_asset_duration",
                "key": f"{asset}|{duration}",
                "rows": "",
                "count": d["count"],
                "amount_usd": round(d["usd"], 6),
                "pnl_usd": "",
                "roi_pct": "",
                "extra": f"shares={round(d['shares'], 6)}",
            }
        )

    for name, bucket in (
        ("closed_by_duration", by_duration),
        ("closed_by_asset", by_asset),
        ("closed_by_outcome", by_outcome),
    ):
        for key, d in sorted(bucket.items(), key=lambda x: x[1]["bought"], reverse=True):
            csv_rows.append(
                {
                    "section": name,
                    "key": key,
                    "rows": d["rows"],
                    "count": "",
                    "amount_usd": round(d["bought"], 6),
                    "pnl_usd": round(d["pnl"], 6),
                    "roi_pct": round(_pct(d["pnl"], d["bought"]), 6),
                    "extra": "",
                }
            )

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["section", "key", "rows", "count", "amount_usd", "pnl_usd", "roi_pct", "extra"],
        )
        w.writeheader()
        w.writerows(csv_rows)

    lines = []
    lines.append("BUY/SELL STRATEGY PROFILE (KYIV DAY)")
    lines.append("=" * 110)
    lines.append(f"wallet: {settings.target_wallet}")
    lines.append(f"day_kyiv: {date_kyiv}")
    lines.append(f"activity_rows: {len(day_activity)}")
    lines.append(f"closed_rows: {closed_total}")
    lines.append("")
    lines.append("ACTIVITY FLOW")
    lines.append("-" * 110)
    for action, c in sorted(action_counts.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"{action}: {c}")
    lines.append(f"buy_amount_avg_usd: {round(buy_amount_avg, 6)}")
    lines.append(f"buy_amount_median_usd: {round(buy_amount_median, 6)}")
    lines.append(f"buy_size_avg: {round(buy_size_avg, 6)}")
    lines.append(f"buy_size_median: {round(buy_size_median, 6)}")
    lines.append("")
    lines.append("CLOSED RESULTS")
    lines.append("-" * 110)
    lines.append(f"closed_total_bought_usd: {round(closed_total_bought, 6)}")
    lines.append(f"closed_realized_pnl_usd: {round(closed_total_pnl, 6)}")
    lines.append(f"closed_realized_roi_pct: {round(closed_roi, 6)}")
    lines.append(f"closed_wins: {closed_wins}")
    lines.append(f"closed_losses: {closed_losses}")
    lines.append(f"closed_flats: {closed_flats}")
    lines.append(f"closed_winrate_pct: {round(closed_winrate, 6)}")
    lines.append("")
    lines.append("BUY FLOW BY ASSET|DURATION (TOP 20 BY USD)")
    lines.append("-" * 110)
    for (asset, duration), d in sorted(buy_asset_duration.items(), key=lambda x: x[1]["usd"], reverse=True)[:20]:
        lines.append(
            f"{asset}|{duration} | count={d['count']} | buy_usd={round(d['usd'], 6)} | shares={round(d['shares'], 6)}"
        )
    lines.append("")
    lines.append("CLOSED BY DURATION")
    lines.append("-" * 110)
    for key, d in sorted(by_duration.items(), key=lambda x: x[1]["bought"], reverse=True):
        lines.append(
            f"{key} | rows={d['rows']} | bought={round(d['bought'], 6)} | pnl={round(d['pnl'], 6)} | roi={round(_pct(d['pnl'], d['bought']), 6)}%"
        )
    lines.append("")
    lines.append("CLOSED BY ASSET")
    lines.append("-" * 110)
    for key, d in sorted(by_asset.items(), key=lambda x: x[1]["bought"], reverse=True):
        lines.append(
            f"{key} | rows={d['rows']} | bought={round(d['bought'], 6)} | pnl={round(d['pnl'], 6)} | roi={round(_pct(d['pnl'], d['bought']), 6)}%"
        )
    lines.append("")
    lines.append("CLOSED BY OUTCOME")
    lines.append("-" * 110)
    for key, d in sorted(by_outcome.items(), key=lambda x: x[1]["bought"], reverse=True):
        lines.append(
            f"{key} | rows={d['rows']} | bought={round(d['bought'], 6)} | pnl={round(d['pnl'], 6)} | roi={round(_pct(d['pnl'], d['bought']), 6)}%"
        )

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(txt_path), str(csv_path)

