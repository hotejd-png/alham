import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.utils import parse_dt, safe_float


def _kyiv_tz():
    try:
        return ZoneInfo("Europe/Kyiv")
    except ZoneInfoNotFoundError:
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


def _find_closed_csv(wallet_dir: Path) -> Path | None:
    p1 = wallet_dir / "positions_reports" / "closed_positions_latest.csv"
    if p1.exists():
        return p1
    p2 = wallet_dir / "csv" / "closed_positions_latest.csv"
    if p2.exists():
        return p2
    return None


def _classify_strategy(action_counts: Counter, closed_rows_count: int) -> tuple[str, float]:
    buy = action_counts.get("BUY", 0)
    sell = action_counts.get("SELL", 0)
    merge = action_counts.get("MERGE", 0)
    redeem = action_counts.get("REDEEM", 0)
    total = buy + sell + merge + redeem
    if total == 0:
        return "NO_DATA", 0.0

    merge_share = (merge + redeem) / total
    buy_sell_share = (buy + sell) / total
    merge_buy_ratio = (merge / buy) if buy > 0 else 0.0
    merge_redeem_buy_ratio = ((merge + redeem) / buy) if buy > 0 else 0.0

    # Heavy merger profile:
    # - either large merge share in total stream
    # - or very high absolute merge activity with meaningful merge/buy ratio.
    if (
        (merge_share >= 0.25 and merge >= max(50, int(buy * 0.08)))
        or (merge >= 1000 and merge_buy_ratio >= 0.03)
        or (merge >= 1500 and merge_redeem_buy_ratio >= 0.05)
    ):
        confidence = min(1.0, 0.55 + merge_share)
        return "MERGE_HEAVY", round(confidence, 3)

    if buy_sell_share >= 0.9 and merge_share <= 0.1 and closed_rows_count > 0:
        confidence = min(1.0, 0.55 + buy_sell_share * 0.4)
        return "BUY_SELL", round(confidence, 3)

    confidence = min(1.0, 0.45 + abs(buy_sell_share - merge_share))
    return "MIXED", round(confidence, 3)


def _best_timeframe(closed_by_duration: dict[str, dict]) -> str:
    best = "n/a"
    best_roi = float("-inf")
    for tf, d in closed_by_duration.items():
        bought = d.get("bought", 0.0)
        if bought < 100.0:
            continue
        roi = (d.get("pnl", 0.0) / bought * 100.0) if bought > 0 else 0.0
        if roi > best_roi:
            best_roi = roi
            best = tf
    return best


def write_auto_account_analysis(
    settings,
    date_kyiv: str | None = None,
    base_multi_wallet_dir: str = "data/multi_wallet",
    out_dir: str = "data/analysis",
) -> tuple[str, str]:
    if not date_kyiv:
        date_kyiv = datetime.now(_kyiv_tz()).strftime("%Y-%m-%d")

    base = Path(base_multi_wallet_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    csv_path = out / f"auto_account_analysis_{date_kyiv}.csv"
    txt_path = out / f"auto_account_analysis_{date_kyiv}.txt"

    rows_out = []
    lines = []
    lines.append(f"AUTO ACCOUNT ANALYSIS (KYIV DAY={date_kyiv})")
    lines.append("=" * 120)

    wallet_dirs = sorted([p for p in base.iterdir() if p.is_dir() and p.name.startswith("0x")])
    by_strategy_base = out / "by_strategy" / date_kyiv
    by_strategy_base.mkdir(parents=True, exist_ok=True)

    for wallet_dir in wallet_dirs:
        wallet = wallet_dir.name
        action_counts = Counter()

        activity_csv = wallet_dir / "account_logs" / wallet / "activity_all.csv"
        if activity_csv.exists():
            with activity_csv.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    if _kyiv_day(r.get("timestamp", "")) != date_kyiv:
                        continue
                    action = (r.get("action_class") or "").upper()
                    if action:
                        action_counts[action] += 1

        closed_by_duration = defaultdict(lambda: {"rows": 0, "bought": 0.0, "pnl": 0.0})
        closed_rows_count = 0
        closed_total_bought = 0.0
        closed_total_pnl = 0.0
        closed_wins = 0
        closed_losses = 0

        closed_csv = _find_closed_csv(wallet_dir)
        if closed_csv is not None:
            with closed_csv.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    if _kyiv_day(r.get("snapshot_time", "")) != date_kyiv:
                        continue
                    slug = r.get("market_slug") or r.get("event_slug") or ""
                    tf = _duration_bucket(slug)
                    bought = safe_float(r.get("total_bought"))
                    pnl = safe_float(r.get("realized_pnl"))
                    closed_rows_count += 1
                    closed_total_bought += bought
                    closed_total_pnl += pnl
                    if pnl > 0:
                        closed_wins += 1
                    elif pnl < 0:
                        closed_losses += 1
                    d = closed_by_duration[tf]
                    d["rows"] += 1
                    d["bought"] += bought
                    d["pnl"] += pnl

        strategy_type, confidence = _classify_strategy(action_counts, closed_rows_count)
        best_tf = _best_timeframe(closed_by_duration)
        closed_roi_pct = (closed_total_pnl / closed_total_bought * 100.0) if closed_total_bought > 0 else 0.0
        closed_winrate_pct = (closed_wins / closed_rows_count * 100.0) if closed_rows_count > 0 else 0.0

        row = {
            "day_kyiv": date_kyiv,
            "wallet": wallet,
            "strategy_type": strategy_type,
            "confidence": confidence,
            "best_timeframe": best_tf,
            "activity_buy": action_counts.get("BUY", 0),
            "activity_sell": action_counts.get("SELL", 0),
            "activity_merge": action_counts.get("MERGE", 0),
            "activity_redeem": action_counts.get("REDEEM", 0),
            "closed_rows": closed_rows_count,
            "closed_bought_usd": round(closed_total_bought, 6),
            "closed_realized_pnl_usd": round(closed_total_pnl, 6),
            "closed_realized_roi_pct": round(closed_roi_pct, 6),
            "closed_winrate_pct": round(closed_winrate_pct, 6),
        }
        rows_out.append(row)

        lines.append(
            f"{wallet} | strategy={strategy_type} (conf={confidence}) | best_tf={best_tf} | "
            f"BUY={row['activity_buy']} SELL={row['activity_sell']} MERGE={row['activity_merge']} REDEEM={row['activity_redeem']} | "
            f"closed={closed_rows_count} roi={round(closed_roi_pct, 4)}% winrate={round(closed_winrate_pct, 2)}%"
        )

        strategy_dir = by_strategy_base / strategy_type
        strategy_dir.mkdir(parents=True, exist_ok=True)
        wallet_txt = strategy_dir / f"{wallet}.txt"
        wallet_txt.write_text(
            "\n".join(
                [
                    f"wallet: {wallet}",
                    f"day_kyiv: {date_kyiv}",
                    f"strategy_type: {strategy_type}",
                    f"confidence: {confidence}",
                    f"best_timeframe: {best_tf}",
                    f"activity_buy: {row['activity_buy']}",
                    f"activity_sell: {row['activity_sell']}",
                    f"activity_merge: {row['activity_merge']}",
                    f"activity_redeem: {row['activity_redeem']}",
                    f"closed_rows: {closed_rows_count}",
                    f"closed_bought_usd: {row['closed_bought_usd']}",
                    f"closed_realized_pnl_usd: {row['closed_realized_pnl_usd']}",
                    f"closed_realized_roi_pct: {row['closed_realized_roi_pct']}",
                    f"closed_winrate_pct: {row['closed_winrate_pct']}",
                ]
            ),
            encoding="utf-8",
        )

    if rows_out:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            writer.writeheader()
            writer.writerows(rows_out)
    else:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "day_kyiv",
                    "wallet",
                    "strategy_type",
                    "confidence",
                    "best_timeframe",
                    "activity_buy",
                    "activity_sell",
                    "activity_merge",
                    "activity_redeem",
                    "closed_rows",
                    "closed_bought_usd",
                    "closed_realized_pnl_usd",
                    "closed_realized_roi_pct",
                    "closed_winrate_pct",
                ]
            )

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(txt_path), str(csv_path)
