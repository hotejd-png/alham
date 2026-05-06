from clients.http_client import HttpClient

class DataAPI:
    def __init__(self, base_url: str, endpoints: dict, timeout_seconds: int, user_agent: str, max_retries: int, retry_sleep_seconds: int):
        self.base_url = base_url.rstrip("/")
        self.endpoints = endpoints
        self.http = HttpClient(
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
            max_retries=max_retries,
            retry_sleep_seconds=retry_sleep_seconds,
        )

    def _url(self, key: str) -> str:
        return f"{self.base_url}{self.endpoints[key]}"

    def get_activity(self, wallet: str, limit: int = 100, offset: int = 0):
        return self.http.get_json(self._url("activity"), params={"user": wallet, "limit": limit, "offset": offset})

    def get_trades(self, wallet: str, limit: int = 100, offset: int = 0):
        return self.http.get_json(self._url("trades"), params={"user": wallet, "limit": limit, "offset": offset})

    def get_positions(self, wallet: str, limit: int = 50, offset: int = 0):
        return self.http.get_json(self._url("positions"), params={"user": wallet, "limit": limit, "offset": offset})

    def get_closed_positions(self, wallet: str, limit: int = 50, offset: int = 0):
        return self.http.get_json(
            self._url("closed_positions"),
            params={
                "user": wallet,
                "limit": limit,
                "offset": offset,
                "sortBy": "timestamp",
                "order": "desc",
            },
        )
