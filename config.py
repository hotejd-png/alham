import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BaseUrls:
    data_api: str


@dataclass
class Endpoints:
    activity: str
    trades: str
    positions: str
    closed_positions: str


@dataclass
class HttpSettings:
    timeout_seconds: int
    poll_interval_seconds: float
    user_agent: str
    max_retries: int
    retry_sleep_seconds: float
    burst_poll_interval_seconds: float = 0.3
    burst_window_seconds: int = 20
    burst_pages_extra: int = 3


@dataclass
class StorageSettings:
    db_path: str
    raw_dir: str
    log_path: str
    csv_dir: str
    raw_retention_days: int = 3
    raw_max_file_mb: int = 512
    raw_max_total_gb: int = 20
    window_logs_dir: str = "data/window_logs"
    positions_reports_dir: str = "data/positions_reports"
    account_logs_dir: str = "data/account_logs"


@dataclass
class FilterSettings:
    only_five_minute_crypto: bool
    keywords_any: list
    keywords_window: list


class PaginationSettings:
    def __init__(
        self,
        activity_limit,
        trades_limit,
        positions_limit,
        max_pages_per_run,
        max_trade_offset,
        max_activity_offset=3000,
        live_activity_pages=5,
        live_stop_if_page_has_no_new_rows=True
    ):
        self.activity_limit = activity_limit
        self.trades_limit = trades_limit
        self.positions_limit = positions_limit
        self.max_pages_per_run = max_pages_per_run
        self.max_trade_offset = max_trade_offset
        self.max_activity_offset = max_activity_offset
        self.live_activity_pages = live_activity_pages
        self.live_stop_if_page_has_no_new_rows = live_stop_if_page_has_no_new_rows


@dataclass
class FeatureFlags:
    fetch_positions: bool
    fetch_closed_positions: bool
    fetch_trades_in_live: bool
    fetch_trades_in_historical: bool


@dataclass
class LiveSettings:
    session_only: bool = True
    drop_rows_without_timestamp: bool = True
    startup_lookback_seconds: int = 0
    reconcile_every_cycles: int = 15
    reconcile_pages: int = 6
    positions_refresh_cycles: int = 20
    positions_pages: int = 2
    refresh_recent_windows_after_positions: bool = True
    recent_windows_refresh_minutes: int = 30


@dataclass
class AlertsSettings:
    enabled: bool = True
    heartbeat_path: str = "data/alerts/heartbeat.json"
    alerts_log_path: str = "data/alerts/alerts.log"
    no_new_rows_minutes: int = 10
    api_error_streak_threshold: int = 3
    alert_cooldown_minutes: int = 15
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class BackfillSettings:
    resume_from_checkpoint: bool = True
    checkpoint_overlap_pages: int = 1
    checkpoint_path: str = "data/state/backfill_activity_checkpoint.json"


@dataclass
class Settings:
    target_wallet: str
    base_urls: BaseUrls
    endpoints: Endpoints
    http: HttpSettings
    storage: StorageSettings
    filters: FilterSettings
    pagination: PaginationSettings
    features: FeatureFlags
    live: LiveSettings
    alerts: AlertsSettings
    backfill: BackfillSettings


def load_settings(path: str = "settings.json") -> Settings:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Settings file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return Settings(
        target_wallet=raw["target_wallet"],
        base_urls=BaseUrls(**raw["base_urls"]),
        endpoints=Endpoints(**raw["endpoints"]),
        http=HttpSettings(**raw["http"]),
        storage=StorageSettings(**raw["storage"]),
        filters=FilterSettings(**raw["filters"]),
        pagination=PaginationSettings(**raw["pagination"]),
        features=FeatureFlags(**raw["features"]),
        live=LiveSettings(**raw.get("live", {})),
        alerts=AlertsSettings(**raw.get("alerts", {})),
        backfill=BackfillSettings(**raw.get("backfill", {})),
    )


settings = load_settings()
