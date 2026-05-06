import argparse
import re
from pathlib import Path
from config import settings
from logger import get_logger
from storage.db import Database
from core.poller import run_historical_backfill, run_live_loop
from core.window_recorder import WindowRecorder
from core.reporter import (
    print_activity_report,
    print_windows_report,
    print_timeline_report,
    print_merge_report,
    print_day_report,
    print_tracker_report,
    export_csv_reports,
)
from core.slug_report import print_slug_report
from core.positions_report import write_closed_positions_report
from core.daily_wallet_report import write_daily_wallet_report
from core.strategy_profile import write_buy_sell_strategy_profile
from core.account_auto_analysis import write_auto_account_analysis
from core.whale_like_replay import ReplayConfig, run_whale_like_replay


def _safe_wallet_name(wallet: str) -> str:
    text = (wallet or "").strip().lower()
    return re.sub(r"[^a-z0-9]", "_", text)


def _apply_wallet_profile(runtime_settings, wallet: str | None):
    if not wallet:
        return

    wallet = wallet.strip()
    if not wallet:
        return

    safe = _safe_wallet_name(wallet)
    base = Path("data") / "multi_wallet" / safe

    runtime_settings.target_wallet = wallet
    runtime_settings.storage.db_path = str(base / "spy_v3.db")
    runtime_settings.storage.raw_dir = str(base / "raw")
    runtime_settings.storage.csv_dir = str(base / "csv")
    runtime_settings.storage.window_logs_dir = str(base / "window_logs")
    runtime_settings.storage.positions_reports_dir = str(base / "positions_reports")
    runtime_settings.storage.account_logs_dir = str(base / "account_logs")
    runtime_settings.storage.log_path = str(Path("logs") / f"spy_v3_{safe}.log")
    runtime_settings.alerts.heartbeat_path = str(base / "alerts" / "heartbeat.json")
    runtime_settings.alerts.alerts_log_path = str(base / "alerts" / "alerts.log")
    runtime_settings.backfill.checkpoint_path = str(base / "state" / "backfill_activity_checkpoint.json")


def main():
    parser = argparse.ArgumentParser(description="Polymarket spy bot v3")
    sub = parser.add_subparsers(dest="command", required=True)

    p_hist = sub.add_parser("historical", help="Run historical backfill")
    p_hist.add_argument("--pages", type=int, default=settings.pagination.max_pages_per_run)
    p_hist.add_argument("--wallet", type=str, default=None, help="Override wallet address")
    p_hist.add_argument("--reset-checkpoint", action="store_true", help="Reset backfill checkpoint and start from offset 0")

    p_live = sub.add_parser("live", help="Run continuous live monitoring")
    p_live.add_argument("--wallet", type=str, default=None, help="Override wallet address")

    p_act = sub.add_parser("activity-report", help="Print grouped activity report")
    p_act.add_argument("--limit", type=int, default=20)
    p_act.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_win = sub.add_parser("windows-report", help="Print grouped 5-minute windows report")
    p_win.add_argument("--limit", type=int, default=50)
    p_win.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_tl = sub.add_parser("timeline-report", help="Print detailed event timeline")
    p_tl.add_argument("--limit", type=int, default=200)
    p_tl.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_merge = sub.add_parser("merge-report", help="Analyze merge events and estimated edge/profit")
    p_merge.add_argument("--limit", type=int, default=50)
    p_merge.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_day = sub.add_parser("day-report", help="Print grouped day-by-day report")
    p_day.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_tracker = sub.add_parser("tracker-report", help="Print tracker state by window")
    p_tracker.add_argument("--limit", type=int, default=50)
    p_tracker.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_slug = sub.add_parser("slug-report", help="Detailed report for one window/slug")
    p_slug.add_argument("--window", required=True, help="Example: btc-updown-5m-1774110900")
    p_slug.add_argument("--out-dir", default="data/slug_reports")
    p_slug.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_export = sub.add_parser("export-csv", help="Export CSV reports")
    p_export.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_rebuild = sub.add_parser("rebuild-window-stats", help="Rebuild summary.txt and stats.csv for all tracked windows")
    p_rebuild.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_pos = sub.add_parser("positions-report", help="Build closed positions summary report")
    p_pos.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_daily = sub.add_parser("daily-wallet-report", help="Build daily wallet report (Kyiv time)")
    p_daily.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_strategy = sub.add_parser("strategy-profile", help="Build buy/sell strategy profile report (Kyiv day)")
    p_strategy.add_argument("--date", type=str, default=None, help="Kyiv date in YYYY-MM-DD (default: today)")
    p_strategy.add_argument("--wallet", type=str, default=None, help="Use wallet-specific storage profile")

    p_auto = sub.add_parser("account-auto-analysis", help="Auto-analyze all wallets and classify strategy type")
    p_auto.add_argument("--date", type=str, default=None, help="Kyiv date in YYYY-MM-DD (default: today)")

    p_whale_replay = sub.add_parser("whale-replay", help="Replay whale_like profile by window logs")
    p_whale_replay.add_argument("--wallet", type=str, required=True, help="Wallet address (0x...)")
    p_whale_replay.add_argument("--date", type=str, required=True, help="Kyiv date in YYYY-MM-DD")
    p_whale_replay.add_argument("--pair-cost-max", type=float, default=0.985)
    p_whale_replay.add_argument("--pair-cost-min", type=float, default=0.90)
    p_whale_replay.add_argument("--min-events", type=int, default=30)
    p_whale_replay.add_argument("--min-buy-count", type=int, default=20)
    p_whale_replay.add_argument("--min-merge-count", type=int, default=1)
    p_whale_replay.add_argument("--min-merge-coverage-pct", type=float, default=40.0)
    p_whale_replay.add_argument("--window-keyword", type=str, default="5m", help="Window filter, e.g. 5m/15m/1h")
    p_whale_replay.add_argument("--all-windows", action="store_true", help="Disable whale_like filter")

    args = parser.parse_args()
    _apply_wallet_profile(settings, getattr(args, "wallet", None))
    logger = get_logger(settings.storage.log_path)
    db = Database(settings.storage.db_path)
    db.init()

    if args.command == "historical":
        run_historical_backfill(
            settings,
            db,
            logger,
            pages=args.pages,
            wallet_override=args.wallet,
            reset_checkpoint=args.reset_checkpoint,
        )

    elif args.command == "live":
        run_live_loop(settings, db, logger, wallet_override=args.wallet)

    elif args.command == "activity-report":
        print_activity_report(db, limit=args.limit)

    elif args.command == "windows-report":
        print_windows_report(db, settings, limit=args.limit)

    elif args.command == "timeline-report":
        print_timeline_report(db, settings, limit=args.limit)

    elif args.command == "merge-report":
        print_merge_report(db, settings, limit=args.limit)

    elif args.command == "day-report":
        print_day_report(db)

    elif args.command == "tracker-report":
        print_tracker_report(db, settings, limit=args.limit)

    elif args.command == "slug-report":
        print_slug_report(db, window_key=args.window, out_dir=args.out_dir)

    elif args.command == "export-csv":
        export_csv_reports(db, settings)

    elif args.command == "rebuild-window-stats":
        recorder = WindowRecorder(base_dir=settings.storage.window_logs_dir, db=db)
        windows = db.fetch_distinct_windows()
        rebuilt = recorder.rebuild_windows(windows)

        rebuilt_account = 0
        wallet_for_account = (getattr(args, "wallet", None) or settings.target_wallet or "").strip()
        if wallet_for_account:
            wallet_dir = wallet_for_account.lower().replace(":", "_")
            account_window_dir = Path(settings.storage.account_logs_dir) / wallet_dir / "window_logs"
            account_recorder = WindowRecorder(base_dir=str(account_window_dir), db=db)
            rebuilt_account = account_recorder.rebuild_windows(windows)

        print(f"Rebuilt window stats for {rebuilt} windows")
        if rebuilt_account:
            print(f"Rebuilt account window stats for {rebuilt_account} windows")

    elif args.command == "positions-report":
        summary_path, csv_path = write_closed_positions_report(
            db,
            out_dir=settings.storage.positions_reports_dir,
        )
        print(f"Closed positions report: {summary_path}")
        print(f"Closed positions csv: {csv_path}")

    elif args.command == "daily-wallet-report":
        summary_path, csv_path = write_daily_wallet_report(
            db,
            settings,
            out_dir=settings.storage.positions_reports_dir,
        )
        print(f"Daily wallet report: {summary_path}")
        print(f"Daily wallet csv: {csv_path}")

    elif args.command == "strategy-profile":
        summary_path, csv_path = write_buy_sell_strategy_profile(
            db,
            settings,
            out_dir=settings.storage.positions_reports_dir,
            date_kyiv=args.date,
        )
        print(f"Strategy profile report: {summary_path}")
        print(f"Strategy profile csv: {csv_path}")

    elif args.command == "account-auto-analysis":
        summary_path, csv_path = write_auto_account_analysis(
            settings,
            date_kyiv=args.date,
            base_multi_wallet_dir="data/multi_wallet",
            out_dir="data/analysis",
        )
        print(f"Auto account analysis report: {summary_path}")
        print(f"Auto account analysis csv: {csv_path}")

    elif args.command == "whale-replay":
        cfg = ReplayConfig(
            pair_cost_max=args.pair_cost_max,
            pair_cost_min=args.pair_cost_min,
            min_events=args.min_events,
            min_merge_count=args.min_merge_count,
            min_buy_count=args.min_buy_count,
            min_merge_coverage_pct=args.min_merge_coverage_pct,
            only_whale_like=(not args.all_windows),
            window_keyword=args.window_keyword,
        )
        summary_path, csv_path = run_whale_like_replay(
            wallet=args.wallet,
            day_kyiv=args.date,
            base_multi_wallet_dir="data/multi_wallet",
            out_dir="data/analysis",
            cfg=cfg,
        )
        print(f"Whale replay report: {summary_path}")
        print(f"Whale replay csv: {csv_path}")


if __name__ == "__main__":
    main()
