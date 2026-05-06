import json
from datetime import datetime, timezone
from pathlib import Path

import requests


class AlertManager:
    def __init__(
        self,
        enabled: bool = True,
        heartbeat_path: str = "data/alerts/heartbeat.json",
        alerts_log_path: str = "data/alerts/alerts.log",
        cooldown_minutes: int = 15,
        telegram_enabled: bool = False,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
    ):
        self.enabled = enabled
        self.heartbeat_path = Path(heartbeat_path)
        self.alerts_log_path = Path(alerts_log_path)
        self.cooldown_seconds = max(0, int(cooldown_minutes) * 60)
        self.telegram_enabled = telegram_enabled
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self._last_alert_by_key = {}

        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.alerts_log_path.parent.mkdir(parents=True, exist_ok=True)

    def heartbeat(self, payload: dict) -> None:
        if not self.enabled:
            return
        body = dict(payload)
        body["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self.heartbeat_path.open("w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)

    def alert(self, key: str, message: str) -> bool:
        if not self.enabled:
            return False

        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        last_ts = self._last_alert_by_key.get(key, 0)
        if self.cooldown_seconds > 0 and (now_ts - last_ts) < self.cooldown_seconds:
            return False

        self._last_alert_by_key[key] = now_ts
        line = f"[{now.isoformat()}] ALERT {key} {message}\n"
        with self.alerts_log_path.open("a", encoding="utf-8") as f:
            f.write(line)

        self._send_telegram(f"ALERT [{key}] {message}")
        return True

    def _send_telegram(self, text: str) -> None:
        if not self.telegram_enabled:
            return
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            requests.post(
                url,
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=8,
            )
        except Exception:
            # Never break monitoring loop because of alert delivery failures.
            return

