import csv
import json
from pathlib import Path
from collections import defaultdict

from core.filters import is_target_market, make_window_key
from core.utils import (
    extract_window_start_ts,
    extract_market_end_ts,
    extract_window_duration_seconds,
    iso_from_unix,
    parse_dt,
    seconds_to_window_end,
    format_window_labels,
    window_local_date,
)


class WindowRecorder:
    def __init__(self, base_dir: str = "data/window_logs", db=None):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.stats_grace_before_start_sec = 10
        self.stats_grace_after_end_sec = 30

    def _split_window_key(self, window_key: str) -> tuple[str, str]:
        parts = window_key.split("|")
        market_slug = (parts[0].strip() if parts else window_key.strip()) or ""
        event_slug = (parts[1].strip() if len(parts) > 1 else market_slug) or market_slug
        return market_slug, event_slug

    def _safe_name(self, window_key: str) -> str:
        return window_key.replace(" | ", "__").replace("/", "_").replace("\\", "_")

    def _day_dir(self, window_key: str) -> Path:
        day_dir = window_local_date(window_key, local_tz_name="Europe/Kyiv")
        return self.base_dir / day_dir

    def _window_dir_name(self, window_key: str) -> str:
        # Prefix with start unix timestamp so folders are naturally sorted oldest -> newest.
        safe = self._safe_name(window_key)
        start_ts = extract_window_start_ts(window_key)
        if start_ts is None:
            return safe
        return f"{start_ts:010d}__{safe}"

    def _dated_legacy_window_dir(self, window_key: str) -> Path:
        return self._day_dir(window_key) / self._safe_name(window_key)

    def _dated_prefixed_window_dir(self, window_key: str) -> Path:
        return self._day_dir(window_key) / self._window_dir_name(window_key)

    def _window_dir(self, window_key: str) -> Path:
        # Backward compatible:
        # 1) keep writing to existing legacy day-folder path if it already exists,
        # 2) otherwise use new prefixed path (sortable by time).
        prefixed_path = self._dated_prefixed_window_dir(window_key)
        legacy_day_path = self._dated_legacy_window_dir(window_key)

        if prefixed_path.exists():
            return prefixed_path
        if legacy_day_path.exists():
            return legacy_day_path

        prefixed_path.mkdir(parents=True, exist_ok=True)
        return prefixed_path

    def _legacy_window_dir(self, window_key: str) -> Path:
        return self.base_dir / self._safe_name(window_key)

    def _archived_legacy_window_dir(self, window_key: str) -> Path:
        return self.base_dir / "_legacy_flat" / self._safe_name(window_key)

    def _jsonl_path(self, window_key: str) -> Path:
        return self._window_dir(window_key) / "events.jsonl"

    def _csv_path(self, window_key: str) -> Path:
        return self._window_dir(window_key) / "events.csv"

    def _summary_path(self, window_key: str) -> Path:
        return self._window_dir(window_key) / "summary.txt"

    def _summary_full_path(self, window_key: str) -> Path:
        return self._window_dir(window_key) / "summary_full.txt"

    def _stats_csv_path(self, window_key: str) -> Path:
        return self._window_dir(window_key) / "stats.csv"

    def rebuild_windows(self, window_keys: list[str]) -> int:
        rebuilt = 0
        for window_key in window_keys:
            before_exists = self._summary_path(window_key).exists()
            self._rewrite_summary(window_key)
            after_exists = self._summary_path(window_key).exists()
            if after_exists and (not before_exists or after_exists):
                rebuilt += 1
        return rebuilt

    def record_rows(self, rows: list[dict], settings) -> int:
        written = 0
        touched_windows = set()

        for r in rows:
            if not is_target_market(r, settings):
                continue

            window_key = make_window_key(r)
            touched_windows.add(window_key)

            jsonl_path = self._jsonl_path(window_key)
            csv_path = self._csv_path(window_key)

            with jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

            ts = r.get("timestamp", "")
            start_ts = extract_window_start_ts(window_key)
            dt = parse_dt(ts)
            sec_from_start = ""
            if start_ts is not None and dt is not None:
                sec_from_start = int(dt.timestamp()) - start_ts

            row = {
                "external_id": r.get("external_id", ""),
                "timestamp": ts,
                "action_class": r.get("action_class", ""),
                "activity_type": r.get("activity_type", ""),
                "outcome": r.get("outcome", ""),
                "side": r.get("side", ""),
                "price": r.get("price", 0),
                "size": r.get("size", 0),
                "amount_usd": r.get("amount_usd", 0),
                "sec_from_window_start": sec_from_start,
                "sec_to_window_end": seconds_to_window_end(ts, window_key),
                "market_slug": r.get("market_slug", ""),
                "event_slug": r.get("event_slug", ""),
                "title": r.get("title", ""),
            }

            file_exists = csv_path.exists()
            with csv_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)

            written += 1

        for window_key in touched_windows:
            self._rewrite_summary(window_key)

        return written

    def _load_window_rows(self, window_key: str) -> list[dict]:
        jsonl_path = self._jsonl_path(window_key)
        legacy_day_jsonl_path = self._dated_legacy_window_dir(window_key) / "events.jsonl"
        prefixed_day_jsonl_path = self._dated_prefixed_window_dir(window_key) / "events.jsonl"
        legacy_jsonl_path = self._legacy_window_dir(window_key) / "events.jsonl"
        archived_legacy_jsonl_path = self._archived_legacy_window_dir(window_key) / "events.jsonl"
        rows = []

        paths = []
        if jsonl_path.exists():
            paths.append(jsonl_path)
        if legacy_day_jsonl_path.exists() and legacy_day_jsonl_path not in paths:
            paths.append(legacy_day_jsonl_path)
        if prefixed_day_jsonl_path.exists() and prefixed_day_jsonl_path not in paths:
            paths.append(prefixed_day_jsonl_path)
        if legacy_jsonl_path.exists() and legacy_jsonl_path != jsonl_path:
            paths.append(legacy_jsonl_path)
        if (
            archived_legacy_jsonl_path.exists()
            and archived_legacy_jsonl_path != jsonl_path
            and archived_legacy_jsonl_path != legacy_jsonl_path
        ):
            paths.append(archived_legacy_jsonl_path)
        if not paths:
            return rows

        for path in paths:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue

        dedup = {}
        for r in rows:
            key = (r.get("external_id", ""), r.get("timestamp", ""), r.get("action_class", ""), r.get("size", 0))
            dedup[key] = r
        rows = list(dedup.values())

        rows.sort(key=lambda x: (x.get("timestamp", ""), x.get("external_id", "")))
        return rows

    def _rewrite_stats_csv(self, window_key: str, rows: list[dict]) -> None:
        if not rows:
            return

        start_ts = extract_window_start_ts(window_key)
        end_ts = extract_market_end_ts(window_key)
        et_label, local_label = format_window_labels(window_key)

        seq = 0
        cum_events = 0
        cum_buy = 0
        cum_sell = 0
        cum_merge = 0
        cum_usd = 0.0
        cum_size = 0.0
        cum_up_shares = 0.0
        cum_down_shares = 0.0

        out = []
        for r in rows:
            seq += 1
            cum_events += 1

            action = r.get("action_class", "")
            outcome = str(r.get("outcome", "")).lower()
            size = float(r.get("size", 0) or 0)
            usd = float(r.get("amount_usd", 0) or 0)
            price = float(r.get("price", 0) or 0)

            if action == "BUY":
                cum_buy += 1
                if "up" in outcome or "yes" in outcome:
                    cum_up_shares += size
                elif "down" in outcome or "no" in outcome:
                    cum_down_shares += size
            elif action == "SELL":
                cum_sell += 1
            elif action == "MERGE":
                cum_merge += 1

            cum_usd += usd
            cum_size += size

            ts_text = r.get("timestamp", "")
            dt = parse_dt(ts_text)
            unix_ts = int(dt.timestamp()) if dt else ""
            sec_from_start = ""
            sec_to_end = ""
            if dt and start_ts is not None:
                sec_from_start = unix_ts - start_ts
            if dt and end_ts is not None:
                sec_to_end = end_ts - unix_ts

            out.append(
                {
                    "seq": seq,
                    "external_id": r.get("external_id", ""),
                    "timestamp": ts_text,
                    "unix_ts": unix_ts,
                    "window_key": window_key,
                    "window_et_label": et_label,
                    "window_local_label": local_label,
                    "sec_from_window_start": sec_from_start,
                    "sec_to_window_end": sec_to_end,
                    "action_class": action,
                    "activity_type": r.get("activity_type", ""),
                    "outcome": r.get("outcome", ""),
                    "side": r.get("side", ""),
                    "price": price,
                    "size": size,
                    "amount_usd": usd,
                    "cum_events": cum_events,
                    "cum_buy": cum_buy,
                    "cum_sell": cum_sell,
                    "cum_merge": cum_merge,
                    "cum_usd": round(cum_usd, 6),
                    "cum_size": round(cum_size, 6),
                    "cum_up_shares": round(cum_up_shares, 6),
                    "cum_down_shares": round(cum_down_shares, 6),
                    "cum_pairs_min": round(min(cum_up_shares, cum_down_shares), 6),
                    "market_slug": r.get("market_slug", ""),
                    "event_slug": r.get("event_slug", ""),
                    "title": r.get("title", ""),
                }
            )

        path = self._stats_csv_path(window_key)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            writer.writeheader()
            writer.writerows(out)

    def _rewrite_summary(self, window_key: str):
        rows = self._load_window_rows(window_key)
        if not rows:
            return

        summary_path = self._summary_path(window_key)

        market_start_unix = extract_window_start_ts(window_key)
        market_end_unix = extract_market_end_ts(window_key)
        stats_start_unix = (market_start_unix - self.stats_grace_before_start_sec) if market_start_unix is not None else None
        stats_end_unix = (market_end_unix + self.stats_grace_after_end_sec) if market_end_unix is not None else None

        stats_rows = []
        before_window_rows = []
        after_window_rows = []
        for r in rows:
            dt = parse_dt(r.get("timestamp", ""))
            if dt is None or stats_start_unix is None or stats_end_unix is None:
                stats_rows.append(r)
                continue
            ts_unix = int(dt.timestamp())
            if stats_start_unix <= ts_unix <= stats_end_unix:
                stats_rows.append(r)
            elif ts_unix < stats_start_unix:
                before_window_rows.append(r)
            elif ts_unix > stats_end_unix:
                after_window_rows.append(r)

        if not stats_rows:
            stats_rows = list(rows)

        self._rewrite_stats_csv(window_key, stats_rows)

        first_ts = stats_rows[0].get("timestamp", "")
        last_ts = stats_rows[-1].get("timestamp", "")
        first_dt = parse_dt(first_ts)
        last_dt = parse_dt(last_ts)

        market_start_iso = iso_from_unix(market_start_unix) if market_start_unix else ""
        market_end_iso = iso_from_unix(market_end_unix) if market_end_unix else ""
        et_label, local_label = format_window_labels(window_key)

        first_sec_from_start = ""
        last_sec_from_start = ""
        if market_start_unix is not None and first_dt is not None:
            first_sec_from_start = int(first_dt.timestamp()) - market_start_unix
        if market_start_unix is not None and last_dt is not None:
            last_sec_from_start = int(last_dt.timestamp()) - market_start_unix

        total_events = len(stats_rows)
        buy_count = sum(1 for r in stats_rows if r.get("action_class") == "BUY")
        merge_count = sum(1 for r in stats_rows if r.get("action_class") == "MERGE")
        sell_count = sum(1 for r in stats_rows if r.get("action_class") == "SELL")
        trade_count = buy_count + sell_count

        total_usd = sum(float(r.get("amount_usd", 0) or 0) for r in stats_rows)
        total_size = sum(float(r.get("size", 0) or 0) for r in stats_rows)
        total_buy_usd = sum(float(r.get("amount_usd", 0) or 0) for r in stats_rows if r.get("action_class") == "BUY")
        total_sell_usd = sum(float(r.get("amount_usd", 0) or 0) for r in stats_rows if r.get("action_class") == "SELL")
        total_merge_usd = sum(float(r.get("amount_usd", 0) or 0) for r in stats_rows if r.get("action_class") == "MERGE")
        # Cashflow-style estimate from visible activity rows.
        # Positive means more cash returned than spent in this window.
        estimated_result_usd = (total_sell_usd + total_merge_usd) - total_buy_usd

        first_merge_ts = ""
        merge_sizes = []
        total_merge_size = 0.0

        up_by_price = defaultdict(lambda: {"count": 0, "shares": 0.0, "usd": 0.0})
        down_by_price = defaultdict(lambda: {"count": 0, "shares": 0.0, "usd": 0.0})
        first_minute_rows = []
        last_minute_rows = []

        window_duration_sec = extract_window_duration_seconds(window_key)
        last_minute_start_sec = max(0, window_duration_sec - 60)
        last_minute_end_sec = max(0, window_duration_sec - 1)

        for r in stats_rows:
            action = r.get("action_class", "")
            outcome = str(r.get("outcome", "")).strip().lower()
            price = float(r.get("price", 0) or 0)
            size = float(r.get("size", 0) or 0)
            usd = float(r.get("amount_usd", 0) or 0)
            ts_text = r.get("timestamp", "")
            sec_from_start = None
            dt = parse_dt(ts_text)
            if market_start_unix is not None and dt is not None:
                sec_from_start = int(dt.timestamp()) - market_start_unix
                if 0 <= sec_from_start <= 59:
                    first_minute_rows.append(r)
                elif last_minute_start_sec <= sec_from_start <= last_minute_end_sec:
                    last_minute_rows.append(r)

            if action == "MERGE":
                if not first_merge_ts:
                    first_merge_ts = r.get("timestamp", "")
                merge_sizes.append(size)
                total_merge_size += size

            if action == "BUY":
                if "up" in outcome or "yes" in outcome:
                    key = round(price, 6)
                    up_by_price[key]["count"] += 1
                    up_by_price[key]["shares"] += size
                    up_by_price[key]["usd"] += usd
                elif "down" in outcome or "no" in outcome:
                    key = round(price, 6)
                    down_by_price[key]["count"] += 1
                    down_by_price[key]["shares"] += size
                    down_by_price[key]["usd"] += usd

        total_up_buy_shares = sum(v["shares"] for v in up_by_price.values())
        total_down_buy_shares = sum(v["shares"] for v in down_by_price.values())
        total_up_buy_usd = sum(v["usd"] for v in up_by_price.values())
        total_down_buy_usd = sum(v["usd"] for v in down_by_price.values())
        avg_up_buy_price = (total_up_buy_usd / total_up_buy_shares) if total_up_buy_shares > 0 else 0.0
        avg_down_buy_price = (total_down_buy_usd / total_down_buy_shares) if total_down_buy_shares > 0 else 0.0

        paired_shares = min(total_up_buy_shares, total_down_buy_shares)
        unpaired_up_shares = max(total_up_buy_shares - paired_shares, 0.0)
        unpaired_down_shares = max(total_down_buy_shares - paired_shares, 0.0)

        pair_cost_per_share = avg_up_buy_price + avg_down_buy_price if paired_shares > 0 else 0.0
        paired_buy_cost = paired_shares * pair_cost_per_share
        paired_redeem_value = paired_shares
        theoretical_pair_pnl = paired_redeem_value - paired_buy_cost
        theoretical_pair_roi_pct = (theoretical_pair_pnl / paired_buy_cost * 100.0) if paired_buy_cost > 0 else 0.0

        merged_pairs_est = min(total_merge_size, paired_shares)
        merged_pair_pnl_est = merged_pairs_est * (1.0 - pair_cost_per_share) if merged_pairs_est > 0 else 0.0
        edge_per_pair_now = 1.0 - pair_cost_per_share if paired_shares > 0 else 0.0
        merge_coverage_pct = (merged_pairs_est / paired_shares * 100.0) if paired_shares > 0 else 0.0
        realization_gap_usd = theoretical_pair_pnl - estimated_result_usd

        if edge_per_pair_now > 0 and merge_coverage_pct >= 90 and realization_gap_usd <= 50:
            window_quality = "GOOD"
        elif edge_per_pair_now > 0:
            window_quality = "MID"
        else:
            window_quality = "WEAK"

        lines = []
        lines.append(f"WINDOW: {window_key}")
        lines.append("=" * 100)
        lines.append(f"window_et: {et_label or 'N/A'}")
        lines.append(f"window_local: {local_label or 'N/A'}")
        lines.append(f"window_start_iso: {market_start_iso or 'N/A'}")
        lines.append(f"window_end_iso:   {market_end_iso or 'N/A'}")
        lines.append(f"first_ts: {first_ts}")
        lines.append(f"last_ts:  {last_ts}")
        lines.append(f"first_sec_from_start: {first_sec_from_start}")
        lines.append(f"last_sec_from_start:  {last_sec_from_start}")
        lines.append(f"events:   {total_events}")
        lines.append(f"before_window_events: {len(before_window_rows)}")
        lines.append(f"after_window_events: {len(after_window_rows)}")
        lines.append(f"stats_grace_before_start_sec: {self.stats_grace_before_start_sec}")
        lines.append(f"stats_grace_after_end_sec: {self.stats_grace_after_end_sec}")
        lines.append(f"trades:   {trade_count}")
        lines.append(f"buy:      {buy_count}")
        lines.append(f"sell:     {sell_count}")
        lines.append(f"merge:    {merge_count}")
        lines.append(f"total_usd: {round(total_usd, 6)}")
        lines.append(f"total_size: {round(total_size, 6)}")
        lines.append(f"spent_buy_usd: {round(total_buy_usd, 6)}")
        lines.append(f"returned_sell_usd: {round(total_sell_usd, 6)}")
        lines.append(f"returned_merge_usd: {round(total_merge_usd, 6)}")
        lines.append(f"estimated_result_usd: {round(estimated_result_usd, 6)}")
        lines.append(f"first_merge_ts: {first_merge_ts or 'N/A'}")
        lines.append(f"total_merge_size: {round(total_merge_size, 6)}")
        lines.append("stats_csv: stats.csv")
        lines.append("")

        lines.append("PAIR STATS (BUY SIDE)")
        lines.append("-" * 100)
        lines.append(f"buy_up_shares: {round(total_up_buy_shares, 6)}")
        lines.append(f"buy_down_shares: {round(total_down_buy_shares, 6)}")
        lines.append(f"paired_shares: {round(paired_shares, 6)}")
        lines.append(f"unpaired_up_shares: {round(unpaired_up_shares, 6)}")
        lines.append(f"unpaired_down_shares: {round(unpaired_down_shares, 6)}")
        lines.append(f"avg_up_buy_price: {round(avg_up_buy_price, 6)}")
        lines.append(f"avg_down_buy_price: {round(avg_down_buy_price, 6)}")
        lines.append(f"pair_cost_per_share: {round(pair_cost_per_share, 6)}")
        lines.append("")

        lines.append("PNL ESTIMATES")
        lines.append("-" * 100)
        lines.append(f"paired_buy_cost_usd: {round(paired_buy_cost, 6)}")
        lines.append(f"paired_redeem_value_usd: {round(paired_redeem_value, 6)}")
        lines.append(f"theoretical_pair_pnl_usd: {round(theoretical_pair_pnl, 6)}")
        lines.append(f"theoretical_pair_roi_pct: {round(theoretical_pair_roi_pct, 4)}")
        lines.append(f"merged_pairs_est_shares: {round(merged_pairs_est, 6)}")
        lines.append(f"merged_pair_pnl_est_usd: {round(merged_pair_pnl_est, 6)}")
        lines.append(f"edge_per_pair_now: {round(edge_per_pair_now, 6)}")
        lines.append(f"merge_coverage_pct: {round(merge_coverage_pct, 2)}")
        lines.append(f"realization_gap_usd: {round(realization_gap_usd, 6)}")
        lines.append(f"window_quality: {window_quality}")
        lines.append("")

        market_slug, event_slug = self._split_window_key(window_key)
        closed_rows = []
        if self.db is not None:
            try:
                closed_rows = self.db.fetch_latest_closed_positions_for_slugs(
                    market_slug=market_slug,
                    event_slug=event_slug,
                    limit=200,
                )
            except Exception:
                closed_rows = []

        lines.append("CLOSED POSITIONS (THIS SLUG)")
        lines.append("-" * 100)
        if closed_rows:
            closed_ts_values = [r.get("snapshot_time", "") for r in closed_rows if r.get("snapshot_time")]
            first_closed_ts = min(closed_ts_values) if closed_ts_values else ""
            execution_delay_sec = ""
            first_closed_dt = parse_dt(first_closed_ts) if first_closed_ts else None
            if first_closed_dt is not None and market_end_unix is not None:
                execution_delay_sec = int(first_closed_dt.timestamp()) - market_end_unix

            total_closed = len(closed_rows)
            total_bought_closed = sum(float(r.get("total_bought", 0) or 0) for r in closed_rows)
            total_pnl_closed = sum(float(r.get("realized_pnl", 0) or 0) for r in closed_rows)
            total_settlement = total_bought_closed + total_pnl_closed
            win_count = sum(1 for r in closed_rows if float(r.get("realized_pnl", 0) or 0) > 0)
            loss_count = sum(1 for r in closed_rows if float(r.get("realized_pnl", 0) or 0) < 0)
            flat_count = total_closed - win_count - loss_count
            roi_closed = (total_pnl_closed / total_bought_closed * 100.0) if total_bought_closed > 0 else 0.0

            lines.append(f"closed_count: {total_closed}")
            lines.append(f"closed_win_count: {win_count}")
            lines.append(f"closed_loss_count: {loss_count}")
            lines.append(f"closed_flat_count: {flat_count}")
            lines.append(f"closed_total_bought_usd: {round(total_bought_closed, 6)}")
            lines.append(f"closed_total_pnl_usd: {round(total_pnl_closed, 6)}")
            lines.append(f"closed_total_settlement_usd: {round(total_settlement, 6)}")
            lines.append(f"closed_roi_pct: {round(roi_closed, 4)}")
            lines.append(f"first_closed_ts: {first_closed_ts or 'N/A'}")
            lines.append(f"execution_delay_sec: {execution_delay_sec if execution_delay_sec != '' else 'N/A'}")
            lines.append("")
            lines.append("LATEST CLOSED (UP TO 20)")
            for r in closed_rows[:20]:
                pnl = float(r.get("realized_pnl", 0) or 0)
                bought = float(r.get("total_bought", 0) or 0)
                settlement = bought + pnl
                lines.append(
                    f"{r.get('snapshot_time', '')} | "
                    f"outcome={r.get('outcome', '')} | "
                    f"size={round(float(r.get('size', 0) or 0), 6)} | "
                    f"bought={round(bought, 6)} | "
                    f"settlement={round(settlement, 6)} | "
                    f"pnl={round(pnl, 6)}"
                )
        else:
            lines.append("No closed positions for this slug yet")
        lines.append("")

        if merge_sizes:
            lines.append("MERGE SIZES")
            lines.append("-" * 100)
            for idx, size in enumerate(merge_sizes, start=1):
                lines.append(f"{idx}. {round(size, 6)}")
            lines.append("")

        lines.append("")
        lines.append("FIRST MINUTE TRADES (0-59s)")
        lines.append("-" * 100)
        lines.append(f"count: {len(first_minute_rows)}")
        if first_minute_rows:
            for r in first_minute_rows:
                ts_text = r.get("timestamp", "")
                sec = ""
                dt = parse_dt(ts_text)
                if market_start_unix is not None and dt is not None:
                    sec = int(dt.timestamp()) - market_start_unix
                lines.append(
                    f"{ts_text} | "
                    f"sec={str(sec):<3} | "
                    f"{r.get('action_class', ''):<6} | "
                    f"{str(r.get('outcome', '')):<10} | "
                    f"price={r.get('price', 0):<10} | "
                    f"size={r.get('size', 0):<10} | "
                    f"usd={r.get('amount_usd', 0):<10}"
                )
        else:
            lines.append("No trades in first minute")

        lines.append("")
        lines.append(f"LAST MINUTE TRADES ({last_minute_start_sec}-{last_minute_end_sec}s)")
        lines.append("-" * 100)
        lines.append(f"count: {len(last_minute_rows)}")
        if last_minute_rows:
            for r in last_minute_rows:
                ts_text = r.get("timestamp", "")
                sec = ""
                dt = parse_dt(ts_text)
                if market_start_unix is not None and dt is not None:
                    sec = int(dt.timestamp()) - market_start_unix
                lines.append(
                    f"{ts_text} | "
                    f"sec={str(sec):<3} | "
                    f"{r.get('action_class', ''):<6} | "
                    f"{str(r.get('outcome', '')):<10} | "
                    f"price={r.get('price', 0):<10} | "
                    f"size={r.get('size', 0):<10} | "
                    f"usd={r.get('amount_usd', 0):<10}"
                )
        else:
            lines.append("No trades in last minute")

        with summary_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # Full version with all transactions, no minute-only truncation.
        full_lines = list(lines)
        full_lines.append("")
        full_lines.append("ALL EVENTS (FULL, NO TRUNCATION)")
        full_lines.append("-" * 100)
        for r in stats_rows:
            ts_text = r.get("timestamp", "")
            sec = ""
            dt = parse_dt(ts_text)
            if market_start_unix is not None and dt is not None:
                sec = int(dt.timestamp()) - market_start_unix
            full_lines.append(
                f"{ts_text} | "
                f"sec={str(sec):<3} | "
                f"{r.get('action_class', ''):<6} | "
                f"{str(r.get('outcome', '')):<10} | "
                f"price={r.get('price', 0):<10} | "
                f"size={r.get('size', 0):<10} | "
                f"usd={r.get('amount_usd', 0):<10} | "
                f"ext_id={r.get('external_id', '')}"
            )

        if before_window_rows:
            full_lines.append("")
            full_lines.append("BEFORE WINDOW EVENTS (OUT OF STATS WINDOW)")
            full_lines.append("-" * 100)
            for r in before_window_rows:
                ts_text = r.get("timestamp", "")
                sec = ""
                dt = parse_dt(ts_text)
                if market_start_unix is not None and dt is not None:
                    sec = int(dt.timestamp()) - market_start_unix
                full_lines.append(
                    f"{ts_text} | "
                    f"sec={str(sec):<3} | "
                    f"{r.get('action_class', ''):<6} | "
                    f"{str(r.get('outcome', '')):<10} | "
                    f"price={r.get('price', 0):<10} | "
                    f"size={r.get('size', 0):<10} | "
                    f"usd={r.get('amount_usd', 0):<10} | "
                    f"ext_id={r.get('external_id', '')}"
                )

        if after_window_rows:
            full_lines.append("")
            full_lines.append("AFTER WINDOW EVENTS (OUT OF STATS WINDOW)")
            full_lines.append("-" * 100)
            for r in after_window_rows:
                ts_text = r.get("timestamp", "")
                sec = ""
                dt = parse_dt(ts_text)
                if market_start_unix is not None and dt is not None:
                    sec = int(dt.timestamp()) - market_start_unix
                full_lines.append(
                    f"{ts_text} | "
                    f"sec={str(sec):<3} | "
                    f"{r.get('action_class', ''):<6} | "
                    f"{str(r.get('outcome', '')):<10} | "
                    f"price={r.get('price', 0):<10} | "
                    f"size={r.get('size', 0):<10} | "
                    f"usd={r.get('amount_usd', 0):<10} | "
                    f"ext_id={r.get('external_id', '')}"
                )

        summary_full_path = self._summary_full_path(window_key)
        with summary_full_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(full_lines))
