import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from clients.data_api import DataAPI
from core.raw_store import append_jsonl
from core.normalizer import (
    normalize_activity_payload,
    normalize_trades_payload,
    normalize_positions_payload,
)
from core.filters import make_window_key
from core.tracker import WindowTracker
from core.window_recorder import WindowRecorder
from core.account_recorder import AccountRecorder
from core.alerts import AlertManager
from core.backfill_checkpoint import BackfillCheckpoint, BackfillCheckpointStore
from core.positions_report import write_closed_positions_report
from core.utils import parse_dt, extract_window_start_ts, extract_window_duration_seconds


def _is_burst_time(now_utc: datetime, burst_window_seconds: int) -> bool:
    if burst_window_seconds <= 0:
        return False
    sec_in_5m = (now_utc.minute % 5) * 60 + now_utc.second
    return sec_in_5m <= burst_window_seconds or sec_in_5m >= (300 - burst_window_seconds)


def _filter_live_rows(rows: list[dict], session_start_dt: datetime, drop_without_ts: bool) -> tuple[list[dict], int]:
    kept = []
    dropped = 0

    for r in rows:
        dt = parse_dt(r.get("timestamp", ""))
        if dt is None:
            if drop_without_ts:
                dropped += 1
                continue
            kept.append(r)
            continue

        if dt >= session_start_dt:
            kept.append(r)
        else:
            dropped += 1

    return kept, dropped


def _ceil_to_next_window_start(session_start_ts: int, duration_sec: int) -> int:
    if duration_sec <= 0:
        return session_start_ts
    return ((session_start_ts + duration_sec - 1) // duration_sec) * duration_sec


def _filter_rows_started_after_session_window(
    rows: list[dict],
    session_start_dt: datetime,
) -> tuple[list[dict], int]:
    """
    Keep only rows whose slug/window starts at or after the next full window boundary
    after live session start. Example:
      - started at 21:28 in a 5m market -> keep from 21:30 windows only.
      - started at 21:28 in a 15m market -> keep from 21:30 windows only.
    """
    kept = []
    dropped = 0
    session_start_ts = int(session_start_dt.timestamp())

    for r in rows:
        window_key = make_window_key(r)
        window_start_ts = extract_window_start_ts(window_key)
        if window_start_ts is None:
            # If we cannot infer window start, keep to avoid data loss.
            kept.append(r)
            continue

        duration_sec = extract_window_duration_seconds(window_key)
        min_allowed_start = _ceil_to_next_window_start(session_start_ts, duration_sec)

        if window_start_ts >= min_allowed_start:
            kept.append(r)
        else:
            dropped += 1

    return kept, dropped


def _store_raw(settings, name: str, payload) -> None:
    append_jsonl(
        settings.storage.raw_dir,
        name,
        payload,
        retention_days=getattr(settings.storage, "raw_retention_days", 3),
        max_file_mb=getattr(settings.storage, "raw_max_file_mb", 512),
        max_total_gb=getattr(settings.storage, "raw_max_total_gb", 20),
    )


def _client(settings):
    return DataAPI(
        base_url=settings.base_urls.data_api,
        endpoints={
            "activity": settings.endpoints.activity,
            "trades": settings.endpoints.trades,
            "positions": settings.endpoints.positions,
            "closed_positions": settings.endpoints.closed_positions,
        },
        timeout_seconds=settings.http.timeout_seconds,
        user_agent=settings.http.user_agent,
        max_retries=settings.http.max_retries,
        retry_sleep_seconds=settings.http.retry_sleep_seconds,
    )


def run_historical_backfill(
    settings,
    db,
    logger,
    pages: int = 10,
    wallet_override: str | None = None,
    reset_checkpoint: bool = False,
):
    api = _client(settings)
    wallet = wallet_override or settings.target_wallet
    account_recorder = AccountRecorder(base_dir=settings.storage.account_logs_dir)
    wallet_dir = wallet.lower().replace(":", "_")
    wallet_window_recorder = WindowRecorder(
        base_dir=str(Path(settings.storage.account_logs_dir) / wallet_dir / "window_logs"),
        db=db,
    )
    checkpoint_store = BackfillCheckpointStore(settings.backfill.checkpoint_path)
    if reset_checkpoint:
        checkpoint_store.clear()

    start_offset = 0
    checkpoint = checkpoint_store.load(wallet)
    if settings.backfill.resume_from_checkpoint and checkpoint is not None:
        overlap_pages = max(0, int(getattr(settings.backfill, "checkpoint_overlap_pages", 1) or 0))
        overlap = overlap_pages * settings.pagination.activity_limit
        start_offset = max(0, int(checkpoint.next_offset) - overlap)
        logger.info(
            "backfill resume checkpoint wallet=%s start_offset=%s next_offset=%s completed=%s oldest_ts=%s",
            wallet,
            start_offset,
            checkpoint.next_offset,
            checkpoint.completed,
            checkpoint.oldest_ts_seen,
        )
    checkpoint_oldest_ts = checkpoint.oldest_ts_seen if checkpoint else ""

    logger.info("historical backfill started for %s", wallet)

    offset = start_offset
    processed_pages = 0
    while processed_pages < pages:
        page = processed_pages

        if offset > settings.pagination.max_activity_offset:
            logger.info(
                "activity backfill stopped at max_activity_offset=%s",
                settings.pagination.max_activity_offset,
            )
            break

        try:
            payload = api.get_activity(
                wallet,
                limit=settings.pagination.activity_limit,
                offset=offset,
            )
            _store_raw(settings, f"activity_page_{page}", payload)
            rows = normalize_activity_payload(payload)
            inserted, new_rows = db.insert_activity(rows)
            account_recorder.record_rows(wallet, new_rows)
            wallet_window_recorder.record_rows(new_rows, settings)
            oldest_ts = min((r.get("timestamp", "") for r in rows if r.get("timestamp")), default="")
            if oldest_ts:
                checkpoint_oldest_ts = oldest_ts
            checkpoint_store.save(
                BackfillCheckpoint(
                    wallet=wallet,
                    next_offset=offset + settings.pagination.activity_limit,
                    oldest_ts_seen=checkpoint_oldest_ts,
                    last_batch_size=len(rows),
                    completed=False,
                )
            )

            logger.info(
                "activity page=%s fetched=%s inserted=%s offset=%s",
                page,
                len(rows),
                inserted,
                offset,
            )

            if not rows:
                checkpoint_store.save(
                    BackfillCheckpoint(
                        wallet=wallet,
                        next_offset=offset,
                        oldest_ts_seen=checkpoint_oldest_ts,
                        last_batch_size=0,
                        completed=True,
                    )
                )
                break

        except Exception as e:
            logger.exception("activity page=%s failed: %s", page, e)
            break

        processed_pages += 1
        offset += settings.pagination.activity_limit

    if settings.features.fetch_trades_in_historical:
        for page in range(pages):
            offset = page * settings.pagination.trades_limit

            if offset > settings.pagination.max_trade_offset:
                logger.info(
                    "trades backfill stopped at max_trade_offset=%s",
                    settings.pagination.max_trade_offset,
                )
                break

            try:
                payload = api.get_trades(
                    wallet,
                    limit=settings.pagination.trades_limit,
                    offset=offset,
                )
                _store_raw(settings, f"trades_page_{page}", payload)
                rows = normalize_trades_payload(payload)
                inserted = db.insert_trades(rows)

                logger.info(
                    "trades page=%s fetched=%s inserted=%s offset=%s",
                    page,
                    len(rows),
                    inserted,
                    offset,
                )

                if not rows:
                    break

            except Exception as e:
                logger.exception("trades page=%s failed: %s", page, e)
                break

    if settings.features.fetch_positions:
        for page in range(2):
            offset = page * settings.pagination.positions_limit
            try:
                payload = api.get_positions(
                    wallet,
                    limit=settings.pagination.positions_limit,
                    offset=offset,
                )
                _store_raw(settings, f"positions_page_{page}", payload)
                rows = normalize_positions_payload(payload, closed=False)
                inserted = db.insert_positions(rows)

                logger.info(
                    "positions page=%s fetched=%s inserted=%s",
                    page,
                    len(rows),
                    inserted,
                )

                if not rows:
                    break

            except Exception as e:
                logger.exception("positions page=%s failed: %s", page, e)
                break

    if settings.features.fetch_closed_positions:
        for page in range(2):
            offset = page * settings.pagination.positions_limit
            try:
                payload = api.get_closed_positions(
                    wallet,
                    limit=settings.pagination.positions_limit,
                    offset=offset,
                )
                _store_raw(settings, f"closed_positions_page_{page}", payload)
                rows = normalize_positions_payload(payload, closed=True)
                inserted = db.insert_positions(rows)

                logger.info(
                    "closed_positions page=%s fetched=%s inserted=%s",
                    page,
                    len(rows),
                    inserted,
                )

                if not rows:
                    break

            except Exception as e:
                logger.exception("closed_positions page=%s failed: %s", page, e)
                break

    if settings.features.fetch_closed_positions:
        try:
            summary_path, csv_path = write_closed_positions_report(
                db,
                out_dir=settings.storage.positions_reports_dir,
            )
            logger.info("closed positions report updated: %s | %s", summary_path, csv_path)
        except Exception as e:
            logger.exception("failed to build closed positions report: %s", e)


def run_live_loop(settings, db, logger, wallet_override: str | None = None):
    api = _client(settings)
    wallet = wallet_override or settings.target_wallet
    logger.info("live monitoring started for %s", wallet)
    session_start_dt = datetime.now(timezone.utc)
    startup_lookback_seconds = max(0, int(getattr(settings.live, "startup_lookback_seconds", 0) or 0))
    session_cutoff_dt = session_start_dt - timedelta(seconds=startup_lookback_seconds)
    logger.info(
        "live session start cutoff=%s session_only=%s startup_lookback_seconds=%s",
        session_cutoff_dt.isoformat(),
        settings.live.session_only,
        startup_lookback_seconds,
    )
    session_start_ts = int(session_start_dt.timestamp())
    next_5m = _ceil_to_next_window_start(session_start_ts, 300)
    next_15m = _ceil_to_next_window_start(session_start_ts, 900)
    next_1h = _ceil_to_next_window_start(session_start_ts, 3600)
    logger.info(
        "live window gate start_ts=%s next_5m=%s next_15m=%s next_1h=%s",
        session_start_dt.isoformat(),
        datetime.fromtimestamp(next_5m, tz=timezone.utc).isoformat(),
        datetime.fromtimestamp(next_15m, tz=timezone.utc).isoformat(),
        datetime.fromtimestamp(next_1h, tz=timezone.utc).isoformat(),
    )

    tracker = WindowTracker(keepalive_after_end_sec=120)
    recorder = WindowRecorder(base_dir=settings.storage.window_logs_dir, db=db)
    account_recorder = AccountRecorder(base_dir=settings.storage.account_logs_dir)
    wallet_dir = wallet.lower().replace(":", "_")
    wallet_window_recorder = WindowRecorder(
        base_dir=str(Path(settings.storage.account_logs_dir) / wallet_dir / "window_logs"),
        db=db,
    )
    positions_refresh_cycles = max(1, int(getattr(settings.live, "positions_refresh_cycles", 20) or 20))
    positions_pages = max(1, int(getattr(settings.live, "positions_pages", 2) or 2))
    refresh_recent_windows_after_positions = bool(
        getattr(settings.live, "refresh_recent_windows_after_positions", True)
    )
    recent_windows_refresh_minutes = max(
        1,
        int(getattr(settings.live, "recent_windows_refresh_minutes", 30) or 30),
    )
    alert_manager = AlertManager(
        enabled=getattr(settings.alerts, "enabled", True),
        heartbeat_path=getattr(settings.alerts, "heartbeat_path", "data/alerts/heartbeat.json"),
        alerts_log_path=getattr(settings.alerts, "alerts_log_path", "data/alerts/alerts.log"),
        cooldown_minutes=getattr(settings.alerts, "alert_cooldown_minutes", 15),
        telegram_enabled=getattr(settings.alerts, "telegram_enabled", False),
        telegram_bot_token=getattr(settings.alerts, "telegram_bot_token", ""),
        telegram_chat_id=getattr(settings.alerts, "telegram_chat_id", ""),
    )
    no_new_rows_minutes = max(1, int(getattr(settings.alerts, "no_new_rows_minutes", 10) or 10))
    error_streak_threshold = max(1, int(getattr(settings.alerts, "api_error_streak_threshold", 3) or 3))
    last_new_rows_dt = datetime.now(timezone.utc)
    api_error_streak = 0

    cycle = 0
    while True:
        cycle += 1
        start_ts = time.time()
        now_utc = datetime.now(timezone.utc)
        burst_mode = _is_burst_time(now_utc, settings.http.burst_window_seconds)
        cycle_poll_interval = (
            settings.http.burst_poll_interval_seconds
            if burst_mode
            else settings.http.poll_interval_seconds
        )

        inserted_total = 0
        fetched_total = 0
        tracker_new_total = 0
        tracker_updated_total = 0
        tracker_rows_total = 0
        recorded_total = 0
        dropped_total = 0
        dropped_by_window_total = 0
        cycle_api_error_count = 0

        max_pages = settings.pagination.live_activity_pages + (
            settings.http.burst_pages_extra if burst_mode else 0
        )
        reconcile_every_cycles = max(0, int(getattr(settings.live, "reconcile_every_cycles", 0) or 0))
        reconcile_pages = max(0, int(getattr(settings.live, "reconcile_pages", 0) or 0))
        reconcile_mode = (
            reconcile_every_cycles > 0
            and reconcile_pages > 0
            and cycle % reconcile_every_cycles == 0
        )
        stop_on_empty_new = settings.pagination.live_stop_if_page_has_no_new_rows

        def process_page(page: int, phase: str = "head") -> tuple[int, int] | None:
            nonlocal cycle_api_error_count
            nonlocal fetched_total
            nonlocal inserted_total
            nonlocal tracker_new_total
            nonlocal tracker_updated_total
            nonlocal tracker_rows_total
            nonlocal recorded_total
            offset = page * settings.pagination.activity_limit

            if offset > settings.pagination.max_activity_offset:
                logger.info(
                    "cycle=%s phase=%s stop at max_activity_offset=%s",
                    cycle,
                    phase,
                    settings.pagination.max_activity_offset,
                )
                return None

            try:
                payload = api.get_activity(
                    wallet,
                    limit=settings.pagination.activity_limit,
                    offset=offset,
                )
                _store_raw(settings, f"activity_live_page_{page}", payload)

                rows = normalize_activity_payload(payload)
                if settings.live.session_only:
                    rows, dropped = _filter_live_rows(
                        rows,
                        session_start_dt=session_cutoff_dt,
                        drop_without_ts=settings.live.drop_rows_without_timestamp,
                    )
                    nonlocal_dropped_total[0] += dropped
                    rows, dropped_by_window = _filter_rows_started_after_session_window(
                        rows,
                        session_start_dt=session_start_dt,
                    )
                    nonlocal_dropped_total[0] += dropped_by_window
                    nonlocal_dropped_by_window_total[0] += dropped_by_window

                fetched_count = len(rows)
                inserted, new_rows = db.insert_activity(rows)

                new_windows, updated_windows = tracker.update_from_rows(new_rows, settings)
                recorded = recorder.record_rows(new_rows, settings)
                account_recorder.record_rows(wallet, new_rows)
                wallet_window_recorder.record_rows(new_rows, settings)

                fetched_total += fetched_count
                inserted_total += inserted
                tracker_new_total += new_windows
                tracker_updated_total += updated_windows
                tracker_rows_total += len(new_rows)
                recorded_total += recorded

                logger.info(
                    "cycle=%s phase=%s page=%s fetched=%s inserted=%s tracker_rows=%s recorded=%s offset=%s tracker_new=%s tracker_updated=%s",
                    cycle,
                    phase,
                    page,
                    fetched_count,
                    inserted,
                    len(new_rows),
                    recorded,
                    offset,
                    new_windows,
                    updated_windows,
                )

                return fetched_count, inserted
            except Exception as e:
                cycle_api_error_count += 1
                logger.exception("cycle=%s phase=%s page=%s live activity failed: %s", cycle, phase, page, e)
                return None

        nonlocal_dropped_total = [0]
        nonlocal_dropped_by_window_total = [0]

        # Head scan: most recent pages each cycle.
        for page in range(max_pages):
            result = process_page(page, phase="head")
            if result is None:
                break
            fetched_count, inserted = result
            if fetched_count == 0:
                break
            if stop_on_empty_new and inserted == 0:
                break

        # Reconcile scan: periodically peek deeper offsets to reduce live offset-shift misses.
        if reconcile_mode:
            start_page = max_pages
            end_page = max_pages + reconcile_pages
            for page in range(start_page, end_page):
                result = process_page(page, phase="reconcile")
                if result is None:
                    break

        dropped_total = nonlocal_dropped_total[0]
        dropped_by_window_total = nonlocal_dropped_by_window_total[0]
        t = tracker.summary()
        elapsed = round(time.time() - start_ts, 3)

        if inserted_total == 0:
            logger.info(
                "cycle=%s heartbeat no_new_rows fetched_total=%s tracker_rows=%s recorded=%s tracked_total=%s active_total=%s elapsed=%ss mode=%s max_pages=%s reconcile=%s poll_interval=%s",
                cycle,
                fetched_total,
                tracker_rows_total,
                recorded_total,
                t["tracked_total"],
                t["active_total"],
                elapsed,
                "burst" if burst_mode else "normal",
                max_pages,
                reconcile_mode,
                cycle_poll_interval,
            )
            if dropped_by_window_total > 0:
                logger.info(
                    "cycle=%s gate_dropped_old_windows=%s",
                    cycle,
                    dropped_by_window_total,
                )
        else:
            last_new_rows_dt = datetime.now(timezone.utc)
            logger.info(
                "cycle=%s total_new_rows=%s fetched_total=%s dropped_total=%s tracker_rows=%s recorded=%s tracked_total=%s active_total=%s elapsed=%ss mode=%s max_pages=%s reconcile=%s poll_interval=%s",
                cycle,
                inserted_total,
                fetched_total,
                dropped_total,
                tracker_rows_total,
                recorded_total,
                t["tracked_total"],
                t["active_total"],
                elapsed,
                "burst" if burst_mode else "normal",
                max_pages,
                reconcile_mode,
                cycle_poll_interval,
            )
            if dropped_by_window_total > 0:
                logger.info(
                    "cycle=%s gate_dropped_old_windows=%s",
                    cycle,
                    dropped_by_window_total,
                )

        # Health-check heartbeat + alerts
        if cycle_api_error_count > 0:
            api_error_streak += 1
        else:
            api_error_streak = 0

        now_dt = datetime.now(timezone.utc)
        idle_minutes = (now_dt - last_new_rows_dt).total_seconds() / 60.0
        heartbeat_payload = {
            "status": "ok",
            "wallet": wallet,
            "cycle": cycle,
            "inserted_total": inserted_total,
            "fetched_total": fetched_total,
            "api_error_count_cycle": cycle_api_error_count,
            "api_error_streak": api_error_streak,
            "idle_minutes_since_new_rows": round(idle_minutes, 2),
            "mode": "burst" if burst_mode else "normal",
            "poll_interval_seconds": cycle_poll_interval,
        }
        alert_manager.heartbeat(heartbeat_payload)

        if idle_minutes >= no_new_rows_minutes:
            alerted = alert_manager.alert(
                key=f"{wallet}:no_new_rows",
                message=(
                    f"no new activity rows for {round(idle_minutes, 1)} minutes; "
                    f"cycle={cycle} fetched_total={fetched_total}"
                ),
            )
            if alerted:
                logger.warning(
                    "ALERT no_new_rows wallet=%s idle_minutes=%s cycle=%s",
                    wallet,
                    round(idle_minutes, 1),
                    cycle,
                )

        if api_error_streak >= error_streak_threshold:
            alerted = alert_manager.alert(
                key=f"{wallet}:api_error_streak",
                message=(
                    f"api error streak reached {api_error_streak} cycles; "
                    f"last_cycle_errors={cycle_api_error_count} cycle={cycle}"
                ),
            )
            if alerted:
                logger.warning(
                    "ALERT api_error_streak wallet=%s streak=%s cycle=%s",
                    wallet,
                    api_error_streak,
                    cycle,
                )

        should_refresh_positions = (
            (settings.features.fetch_positions or settings.features.fetch_closed_positions)
            and (cycle == 1 or cycle % positions_refresh_cycles == 0)
        )

        if should_refresh_positions:
            if settings.features.fetch_positions:
                for page in range(positions_pages):
                    offset = page * settings.pagination.positions_limit
                    try:
                        payload = api.get_positions(
                            wallet,
                            limit=settings.pagination.positions_limit,
                            offset=offset,
                        )
                        _store_raw(settings, f"positions_live_page_{page}", payload)
                        rows = normalize_positions_payload(payload, closed=False)
                        inserted = db.insert_positions(rows)
                        logger.info(
                            "cycle=%s positions page=%s fetched=%s inserted=%s",
                            cycle,
                            page,
                            len(rows),
                            inserted,
                        )
                        if not rows:
                            break
                    except Exception as e:
                        logger.exception("cycle=%s positions page=%s failed: %s", cycle, page, e)
                        break

            if settings.features.fetch_closed_positions:
                for page in range(positions_pages):
                    offset = page * settings.pagination.positions_limit
                    try:
                        payload = api.get_closed_positions(
                            wallet,
                            limit=settings.pagination.positions_limit,
                            offset=offset,
                        )
                        _store_raw(settings, f"closed_positions_live_page_{page}", payload)
                        rows = normalize_positions_payload(payload, closed=True)
                        inserted = db.insert_positions(rows)
                        logger.info(
                            "cycle=%s closed_positions page=%s fetched=%s inserted=%s",
                            cycle,
                            page,
                            len(rows),
                            inserted,
                        )
                        if not rows:
                            break
                    except Exception as e:
                        logger.exception("cycle=%s closed_positions page=%s failed: %s", cycle, page, e)
                        break

                try:
                    summary_path, csv_path = write_closed_positions_report(
                        db,
                        out_dir=settings.storage.positions_reports_dir,
                    )
                    logger.info(
                        "cycle=%s closed positions report updated: %s | %s",
                        cycle,
                        summary_path,
                        csv_path,
                    )
                except Exception as e:
                    logger.exception("cycle=%s failed to build closed positions report: %s", cycle, e)

                if refresh_recent_windows_after_positions:
                    try:
                        now_ts = int(datetime.now(timezone.utc).timestamp())
                        horizon_sec = recent_windows_refresh_minutes * 60
                        recent_keys = []
                        for w in tracker.get_all_windows():
                            end_unix = w.get("market_end_unix")
                            if end_unix is None:
                                continue
                            if 0 <= (now_ts - int(end_unix)) <= horizon_sec:
                                recent_keys.append(w.get("window_key"))

                        if recent_keys:
                            rebuilt_main = recorder.rebuild_windows(recent_keys)
                            rebuilt_wallet = wallet_window_recorder.rebuild_windows(recent_keys)
                            logger.info(
                                "cycle=%s refreshed_recent_windows_for_closed_positions=%s rebuilt_main=%s rebuilt_wallet=%s horizon_min=%s",
                                cycle,
                                len(recent_keys),
                                rebuilt_main,
                                rebuilt_wallet,
                                recent_windows_refresh_minutes,
                            )
                    except Exception as e:
                        logger.exception(
                            "cycle=%s failed to refresh recent windows after closed positions: %s",
                            cycle,
                            e,
                        )

        sleep_for = max(0.0, cycle_poll_interval - elapsed)
        if sleep_for > 0:
            time.sleep(sleep_for)
