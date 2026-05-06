import csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.analyzer import window_summary
from core.utils import parse_dt


def _kyiv_tz():
    try:
        return ZoneInfo("Europe/Kyiv")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=2))


def _to_kyiv_date(ts_iso: str) -> str:
    dt = parse_dt(ts_iso)
    if dt is None:
        return ""
    return dt.astimezone(_kyiv_tz()).strftime("%Y-%m-%d")


def write_daily_wallet_report(db, settings, out_dir: str) -> tuple[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    txt_path = out / "daily_wallet_report.txt"
    csv_path = out / "daily_wallet_report.csv"

    activity_rows = db.fetch_all_activity()
    windows = window_summary(activity_rows, settings)
    closed = db.fetch_latest_closed_positions(limit=50000)

    by_day = defaultdict(
        lambda: {
            "windows_count": 0,
            "invested_usd": 0.0,
            "pairs_shares": 0.0,
            "pair_cost_weighted_sum": 0.0,
            "est_merge_pnl_usd": 0.0,
            "best_slug": "",
            "best_slug_est_pnl_usd": float("-inf"),
            "worst_slug": "",
            "worst_slug_est_pnl_usd": float("inf"),
            "closed_count": 0,
            "closed_wins": 0,
            "closed_losses": 0,
            "closed_flat": 0,
            "closed_bought_usd": 0.0,
            "closed_pnl_usd": 0.0,
        }
    )

    for w in windows:
        day = _to_kyiv_date(w.get("market_start_iso", ""))
        if not day:
            continue
        d = by_day[day]
        d["windows_count"] += 1
        invested = float(w.get("invested_usd", 0) or 0)
        pairs = float(w.get("pairs", 0) or 0)
        pair_cost = float(w.get("pair_cost_per_share", 0) or 0)
        est_pnl = float(w.get("estimated_merge_pnl", 0) or 0)
        d["invested_usd"] += invested
        d["pairs_shares"] += pairs
        d["pair_cost_weighted_sum"] += pair_cost * pairs
        d["est_merge_pnl_usd"] += est_pnl

        key = w.get("window_key", "")
        if est_pnl > d["best_slug_est_pnl_usd"]:
            d["best_slug_est_pnl_usd"] = est_pnl
            d["best_slug"] = key
        if est_pnl < d["worst_slug_est_pnl_usd"]:
            d["worst_slug_est_pnl_usd"] = est_pnl
            d["worst_slug"] = key

    for r in closed:
        day = _to_kyiv_date(r.get("snapshot_time", ""))
        if not day:
            continue
        d = by_day[day]
        d["closed_count"] += 1
        pnl = float(r.get("realized_pnl", 0) or 0)
        bought = float(r.get("total_bought", 0) or 0)
        d["closed_bought_usd"] += bought
        d["closed_pnl_usd"] += pnl
        if pnl > 0:
            d["closed_wins"] += 1
        elif pnl < 0:
            d["closed_losses"] += 1
        else:
            d["closed_flat"] += 1

    rows = []
    for day in sorted(by_day.keys()):
        d = by_day[day]
        avg_pair_cost = (d["pair_cost_weighted_sum"] / d["pairs_shares"]) if d["pairs_shares"] > 0 else 0.0
        closed_winrate = (d["closed_wins"] / d["closed_count"] * 100.0) if d["closed_count"] > 0 else 0.0
        closed_roi = (d["closed_pnl_usd"] / d["closed_bought_usd"] * 100.0) if d["closed_bought_usd"] > 0 else 0.0
        rows.append(
            {
                "day_kyiv": day,
                "windows_count": d["windows_count"],
                "invested_usd": round(d["invested_usd"], 6),
                "avg_pair_cost_per_share": round(avg_pair_cost, 6),
                "est_merge_pnl_usd": round(d["est_merge_pnl_usd"], 6),
                "closed_count": d["closed_count"],
                "closed_wins": d["closed_wins"],
                "closed_losses": d["closed_losses"],
                "closed_flat": d["closed_flat"],
                "closed_winrate_pct": round(closed_winrate, 4),
                "closed_bought_usd": round(d["closed_bought_usd"], 6),
                "closed_pnl_usd": round(d["closed_pnl_usd"], 6),
                "closed_roi_pct": round(closed_roi, 4),
                "best_slug_est_pnl_usd": round(d["best_slug_est_pnl_usd"], 6) if d["best_slug"] else 0.0,
                "best_slug": d["best_slug"],
                "worst_slug_est_pnl_usd": round(d["worst_slug_est_pnl_usd"], 6) if d["worst_slug"] else 0.0,
                "worst_slug": d["worst_slug"],
            }
        )

    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    lines = []
    lines.append("DAILY WALLET REPORT (KYIV TIME)")
    lines.append("=" * 110)
    if not rows:
        lines.append("No data")
    else:
        for r in rows:
            lines.append(
                f"{r['day_kyiv']} | windows={r['windows_count']} | invested={r['invested_usd']} | "
                f"avg_pair_cost={r['avg_pair_cost_per_share']} | est_merge_pnl={r['est_merge_pnl_usd']} | "
                f"closed={r['closed_count']} wins={r['closed_wins']} losses={r['closed_losses']} "
                f"winrate={r['closed_winrate_pct']}% | closed_pnl={r['closed_pnl_usd']} "
                f"roi={r['closed_roi_pct']}%"
            )
            lines.append(
                f"best={r['best_slug_est_pnl_usd']} -> {r['best_slug']} | "
                f"worst={r['worst_slug_est_pnl_usd']} -> {r['worst_slug']}"
            )
            lines.append("-" * 110)

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(txt_path), str(csv_path)
