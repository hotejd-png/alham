import json
from pathlib import Path


class TrackerStateStore:
    def __init__(self, path: str = "data/tracker_state.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, tracker):
        data = {
            "keepalive_after_end_sec": tracker.keepalive_after_end_sec,
            "windows": tracker.windows,
        }
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, tracker):
        if not self.path.exists():
            return tracker

        with self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        tracker.keepalive_after_end_sec = raw.get("keepalive_after_end_sec", tracker.keepalive_after_end_sec)
        tracker.windows = raw.get("windows", {})
        return tracker