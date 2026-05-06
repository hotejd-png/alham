from collections import defaultdict
from core.filters import is_target_market, make_window_key
from core.utils import extract_market_end_ts, parse_dt
from datetime import datetime, timezone


class WindowTracker:
    def __init__(self, keepalive_after_end_sec: int = 120):
        self.keepalive_after_end_sec = keepalive_after_end_sec
        self.windows = {}

    def _now_utc(self):
        return datetime.now(timezone.utc)

    def update_from_rows(self, rows, settings):
        """
        Берет новые activity rows и обновляет состояние по каждому slug/window.
        """
        new_windows = 0
        updated_windows = 0

        for r in rows:
            if not is_target_market(r, settings):
                continue

            key = make_window_key(r)
            ts = r.get("timestamp", "")
            action = r.get("action_class", "OTHER")
            size = float(r.get("size", 0) or 0)
            usd = float(r.get("amount_usd", 0) or 0)

            if key not in self.windows:
                self.windows[key] = {
                    "window_key": key,
                    "first_seen_ts": ts,
                    "last_seen_ts": ts,
                    "event_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "merge_count": 0,
                    "last_action": action,
                    "total_size": 0.0,
                    "total_usd": 0.0,
                    "market_end_unix": extract_market_end_ts(key),
                    "is_active": True,
                }
                new_windows += 1
            else:
                updated_windows += 1

            w = self.windows[key]
            if ts and (not w["first_seen_ts"] or ts < w["first_seen_ts"]):
                w["first_seen_ts"] = ts
            if ts and (not w["last_seen_ts"] or ts > w["last_seen_ts"]):
                w["last_seen_ts"] = ts

            w["event_count"] += 1
            w["last_action"] = action
            w["total_size"] += size
            w["total_usd"] += usd

            if action == "BUY":
                w["buy_count"] += 1
            elif action == "SELL":
                w["sell_count"] += 1
            elif action == "MERGE":
                w["merge_count"] += 1

        self.refresh_activity_flags()
        return new_windows, updated_windows

    def refresh_activity_flags(self):
        now = self._now_utc()

        for _, w in self.windows.items():
            end_unix = w.get("market_end_unix")
            if end_unix is None:
                w["is_active"] = True
                w["seconds_after_end_now"] = None
                continue

            end_dt = datetime.fromtimestamp(end_unix, tz=timezone.utc)
            delta = int((now - end_dt).total_seconds())
            w["seconds_after_end_now"] = delta

            # активно, пока не прошло keepalive_after_end_sec после конца окна
            w["is_active"] = delta <= self.keepalive_after_end_sec

    def get_active_windows(self):
        self.refresh_activity_flags()
        rows = [w for w in self.windows.values() if w.get("is_active")]
        rows.sort(key=lambda x: (x.get("last_seen_ts", ""), x["window_key"]), reverse=True)
        return rows

    def get_all_windows(self):
        self.refresh_activity_flags()
        rows = list(self.windows.values())
        rows.sort(key=lambda x: (x.get("last_seen_ts", ""), x["window_key"]), reverse=True)
        return rows

    def summary(self):
        self.refresh_activity_flags()
        all_rows = list(self.windows.values())
        active_rows = [w for w in all_rows if w.get("is_active")]
        return {
            "tracked_total": len(all_rows),
            "active_total": len(active_rows),
        }