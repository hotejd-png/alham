import time
import requests

class HttpClient:
    def __init__(self, timeout_seconds: int = 20, user_agent: str = "spy-bot-v3/1.0", max_retries: int = 3, retry_sleep_seconds: int = 2):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": user_agent,
        })

    def get_json(self, url: str, params=None):
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, params=params or {}, timeout=self.timeout_seconds)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(self.retry_sleep_seconds)
        raise last_err
