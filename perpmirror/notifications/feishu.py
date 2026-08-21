from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import httpx

from perpmirror.exceptions import RetryableExchangeError
from perpmirror.notifications.base import Notifier


class FeishuNotifier(Notifier):
    def __init__(self, webhook: str, secret: str = "") -> None:
        self._webhook = webhook
        self._secret = secret
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))

    def _signature(self, timestamp: int) -> str:
        string_to_sign = f"{timestamp}\n{self._secret}".encode()
        digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    async def send_card(self, card: dict[str, Any]) -> None:
        payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
        if self._secret:
            timestamp = int(time.time())
            payload.update({"timestamp": str(timestamp), "sign": self._signature(timestamp)})
        try:
            response = await self._client.post(self._webhook, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableExchangeError("Feishu webhook network failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableExchangeError(f"Feishu retryable HTTP status: {response.status_code}")
        if response.status_code >= 400:
            detail = " ".join(response.text.split())[:180]
            raise RuntimeError(f"Feishu HTTP {response.status_code}: {detail or 'request rejected'}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Feishu returned a non-JSON response") from exc
        code = data.get("code", data.get("StatusCode", 0))
        if int(code or 0) != 0:
            message = data.get("msg", data.get("StatusMessage", "request rejected"))
            raise RuntimeError(f"Feishu rejected card ({code}): {message}")

    async def close(self) -> None:
        await self._client.aclose()
