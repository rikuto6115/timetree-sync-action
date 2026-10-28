from __future__ import annotations

from typing import Any

import requests


class TimeTreeClient:
    BASE_URL = "https://timetreeapp.com"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "timetree-sync-action/0.1",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> requests.Response:
        response = self.session.request(
            method=method,
            url=f"{self.BASE_URL}{path}",
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response
