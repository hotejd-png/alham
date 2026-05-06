from pathlib import Path
import csv
from core.analyzer import activity_counts, window_summary, timeline
from core.merge_analyzer import analyze_merges
from core.tracker import WindowTracker
from core.utils import parse_dt


def print_activity_report(db, limit: int = 20):
    rows = db.fetch_all_activity()
    summary = activity_counts(rows)
    print("=" * 90)
    print("ACTIVITY REPORT")
    print("=" * 90)
    for row in summary[:limit]:
        print(
            f"{row['action_class']:<8} | "
            f"count={row['count']:<6} | "
            f"usd={row['usd']:<12} | "
            f"shares={row['shares']:<12}"
        )


def print_windows_report(db, settings, limit: int = 50):
    rows = db.fetch_all_activity()
    summary = window_summary(rows, settings)
    print("=" * 260)
    print("WINDOWS REPORT")
    print("=" * 260)
    for row in summary[:limit]:
        print(
            f"events={row['event_count']:<4} "
            f"buys={row['buy_count']:<4} "
            f"merges={row['merge_count']:<4} "
            f"first_s={str(row['first_sec_from_start']):<4} "
            f"last_s={str(row['last_sec_from_start']):<4} "
            f"pairs={row['pairs']:<10} "
            f"pair_cost={row.get('pair_cost_per_share', ''):<10} "
            f"edge/share={row['edge_per_share']:<10} "
            f"est_pnl={row['estimated_merge_pnl']:<10} "
            f"coverage={row.get('merge_coverage_pct', ''):<6}% "
            f"unpaired={row.get('unpaired_ratio_pct', ''):<6}% "
            f"quality={row.get('window_quality', ''):<5} "
            f"dur={row['duration_sec']:<4}s "
            f"first_merge={str(row['seconds_until_first_merge']):<4}s "
            f"buy_before={row['buys_before_first_merge']:<4} "
            f"buy_after={row['buys_after_first_merge']:<4} "
            f"more_merges={row['merges_after_first']:<3} "
            f"after_merge={str(row['had_buy_after_merge']):<5} "
            f"max_merge={row['max_merge_size']:<10} "
            f"late={str(row['late_action']):<5} "
            f"last={row['last_action']:<6} "
            f"after_end={str(row['last_buy_after_end_sec']):<5} "
            f"{row['window_key']} | ET={row.get('window_et_label', '')} | UA={row.get('window_local_label', '')}"
        )


def print_timeline_report(db, settings, limit: int = 200):
    rows = db.fetch_all_activity()
    tl = timeline(rows, settings)
    print("=" * 220)
    print("TIMELINE REPORT")
    print("=" * 220)
    for row in tl[-limit:]:
        print(
            f"{row['timestamp']} | "
            f"{row['action_class']:<6} | "
            f"{row['outcome']:<10} | "
            f"price={row['price']:<10} | "
            f"size={row['size']:<10} | "
            f"usd={row['amount_usd']:<10} | "
            f"to_end={str(row['secs_to_end']):<5} | "
            f"after_end={str(row['secs_after_end']):<5} | "
            f"{row['window_key']}"
        )


def print_merge_report(db, settings, limit: int = 50):
    rows = db.fetch_all_activity()
    result = analyze_merges(rows, settings)

    print("=" * 160)
    print("MERGE REPORT")
    print("=" * 160)

    for r in result[-limit:]:
        print(
            f"{r['timestamp']} | "
            f"merge_size={r['merge_size']:<10} | "
            f"pairs_before={r['pairs_before_merge']:<10} | "
            f"pairs_used={r['pairs_used']:<10} | "
            f"edge={r['edge']:<10} | "
            f"profit={r['profit']:<10} | "
            f"YES={r['avg_yes']:<10} NO={r['avg_no']:<10} | "
            f"left_yes={r['yes_left']:<10} left_no={r['no_left']:<10} | "
            f"{r['window']}"
        )


def print_day_report(db):
    rows = db.fetch_activity_by_day()

    print("=" * 120)
    print("DAY REPORT")
    print("=" * 120)

    for row in rows:
        print(
            f"{row['day']} | "
            f"events={row['event_count']:<6} | "
            f"buy={row['buy_count']:<6} | "
            f"sell={row['sell_count']:<6} | "
            f"merge={row['merge_count']:<6} | "
            f"usd={row['total_usd']:<14} | "
            f"shares={row['total_shares']:<14}"
        )


def print_tracker_report(db, settings, limit: int = 50):
    """
    Строим tracker заново из БД, а не из tracker_state.json.
    Так отчет всегда корректный и не ломается от старого битого state.
    """
    rows = db.fetch_all_activity()
    tracker = WindowTracker(keepalive_after_end_sec=120)
    tracker.update_from_rows(rows, settings)
    tracked = tracker.get_all_windows()

    print("=" * 180)
    print("TRACKER REPORT")
    print("=" * 180)

    for row in tracked[:limit]:
        print(
            f"active={str(row['is_active']):<5} | "
            f"events={row['event_count']:<5} | "
            f"buy={row['buy_count']:<5} | "
            f"merge={row['merge_count']:<5} | "
            f"usd={round(row['total_usd'], 4):<12} | "
            f"size={round(row['total_size'], 4):<12} | "
            f"after_end_now={str(row.get('seconds_after_end_now')):<6} | "
            f"last={row['last_action']:<6} | "
            f"{row['window_key']}"
        )


def export_csv_reports(db, settings):
    csv_dir = Path(settings.storage.csv_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)

    rows = db.fetch_all_activity()
    windows = window_summary(rows, settings)
    _attach_closed_delay_metrics(windows, db)
    tl = timeline(rows, settings)
    merges = analyze_merges(rows, settings)
    days = db.fetch_activity_by_day()

    tracker = WindowTracker(keepalive_after_end_sec=120)
    tracker.update_from_rows(rows, settings)
    tracked = tracker.get_all_windows()
    closed_positions = db.fetch_latest_closed_positions(limit=5000)
    open_positions = db.fetch_latest_open_positions(limit=5000)

    data_map = {
        "windows_report.csv": windows,
        "timeline_report.csv": tl,
        "merge_report.csv": merges,
        "day_report.csv": days,
        "tracker_report.csv": tracked,
        "closed_positions_latest.csv": closed_positions,
        "open_positions_latest.csv": open_positions,
    }

    for filename, data in data_map.items():
        path = csv_dir / filename
        if data:
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                writer.writeheader()
                writer.writerows(data)

    _export_windows_pretty_report(windows, csv_dir / "windows_report_pretty.txt")

    print(f"CSV exported to: {csv_dir}")


def _export_windows_pretty_report(windows: list[dict], path: Path) -> None:
    lines = []
    lines.append("WINDOWS REPORT (PRETTY)")
    lines.append("=" * 120)

    for idx, row in enumerate(windows, start=1):
        lines.append(f"{idx}. {row.get('window_key', '')}")
        lines.append("-" * 120)
        lines.append(f"window_quality: {row.get('window_quality', '')}")
        lines.append(f"first_closed_ts: {row.get('first_closed_ts', '')}")
        lines.append(f"execution_delay_sec: {row.get('execution_delay_sec', '')}")
        lines.append(f"pair_cost_per_share: {row.get('pair_cost_per_share', '')}")
        lines.append(f"merge_coverage_pct: {row.get('merge_coverage_pct', '')}")
        lines.append(f"unpaired_ratio_pct: {row.get('unpaired_ratio_pct', '')}")
        lines.append(f"merged_pair_pnl_est_usd: {row.get('merged_pair_pnl_est_usd', '')}")
        lines.append(f"unpaired_cost_usd: {row.get('unpaired_cost_usd', '')}")
        lines.append(f"pnl_floor_usd: {row.get('pnl_floor_usd', '')}")
        lines.append(f"invested_usd: {row.get('invested_usd', '')}")
        lines.append(f"pnl_floor_pct: {row.get('pnl_floor_pct', '')}")
        lines.append(f"edge_per_share: {row.get('edge_per_share', '')}")
        lines.append(f"estimated_merge_pnl: {row.get('estimated_merge_pnl', '')}")
        lines.append(f"events: {row.get('event_count', '')}")
        lines.append(f"buy_count: {row.get('buy_count', '')}")
        lines.append(f"sell_count: {row.get('sell_count', '')}")
        lines.append(f"merge_count: {row.get('merge_count', '')}")
        lines.append(f"pairs: {row.get('pairs', '')}")
        lines.append(f"gross_usd: {row.get('gross_usd', '')}")
        lines.append(f"first_ts: {row.get('first_ts', '')}")
        lines.append(f"last_ts: {row.get('last_ts', '')}")
        lines.append(f"first_merge_ts: {row.get('first_merge_ts', '')}")
        lines.append(f"duration_sec: {row.get('duration_sec', '')}")
        lines.append(f"before_window_events: {row.get('before_window_events', '')}")
        lines.append(f"after_window_events: {row.get('after_window_events', '')}")
        lines.append(f"stats_grace_before_start_sec: {row.get('stats_grace_before_start_sec', '')}")
        lines.append(f"stats_grace_after_end_sec: {row.get('stats_grace_after_end_sec', '')}")
        lines.append(f"market_start_iso: {row.get('market_start_iso', '')}")
        lines.append(f"market_end_iso: {row.get('market_end_iso', '')}")
        lines.append(f"window_et_label: {row.get('window_et_label', '')}")
        lines.append(f"window_local_label: {row.get('window_local_label', '')}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _attach_closed_delay_metrics(windows: list[dict], db) -> None:
    first_seen = db.fetch_closed_positions_first_seen()
    by_slug = {}
    for r in first_seen:
        market_slug = (r.get("market_slug") or "").strip()
        event_slug = (r.get("event_slug") or "").strip()
        ts = r.get("first_closed_ts", "")
        if market_slug:
            prev = by_slug.get(market_slug, "")
            if not prev or (ts and ts < prev):
                by_slug[market_slug] = ts
        if event_slug:
            prev = by_slug.get(event_slug, "")
            if not prev or (ts and ts < prev):
                by_slug[event_slug] = ts

    for w in windows:
        window_key = w.get("window_key", "")
        left = window_key.split("|")[0].strip() if window_key else ""
        right = window_key.split("|")[1].strip() if "|" in window_key else left
        first_closed_ts = by_slug.get(left) or by_slug.get(right) or ""
        execution_delay_sec = ""

        end_dt = parse_dt(w.get("market_end_iso", ""))
        closed_dt = parse_dt(first_closed_ts) if first_closed_ts else None
        if end_dt is not None and closed_dt is not None:
            execution_delay_sec = int((closed_dt - end_dt).total_seconds())

        w["first_closed_ts"] = first_closed_ts
        w["execution_delay_sec"] = execution_delay_sec if execution_delay_sec != "" else ""
