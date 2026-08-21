from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import httpx

from perpmirror.exceptions import RetryableExchangeError
from perpmirror.notifications.base import Notifier


class FeishuPayloadRejected(RuntimeError):
    pass


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
        try:
            await self._send_payload({"msg_type": "interactive", "card": card})
        except FeishuPayloadRejected as exc:
            if self._channel_unavailable(str(exc)):
                raise
            # A malformed or temporarily incompatible interactive card must
            # not make an order notification disappear. Plain text is the
            # smallest Feishu payload and acts as a delivery fallback.
            await self._send_payload(
                {
                    "msg_type": "text",
                    "content": {"text": self._plain_text(card)},
                }
            )

    async def _send_payload(self, payload: dict[str, Any]) -> None:
        signed_payload = dict(payload)
        if self._secret:
            timestamp = int(time.time())
            signed_payload.update(
                {"timestamp": str(timestamp), "sign": self._signature(timestamp)}
            )
        try:
            response = await self._client.post(self._webhook, json=signed_payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableExchangeError("Feishu webhook network failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableExchangeError(f"Feishu retryable HTTP status: {response.status_code}")
        if response.status_code >= 400:
            detail = " ".join(response.text.split())[:180]
            raise FeishuPayloadRejected(
                f"Feishu HTTP {response.status_code}: {detail or 'request rejected'}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise FeishuPayloadRejected("Feishu returned a non-JSON response") from exc
        code = data.get("code", data.get("StatusCode", 0))
        if int(code or 0) != 0:
            message = data.get("msg", data.get("StatusMessage", "request rejected"))
            raise FeishuPayloadRejected(f"Feishu rejected payload ({code}): {message}")

    @staticmethod
    def _plain_text(card: dict[str, Any]) -> str:
        title = NotificationCardText.title(card)
        body = NotificationCardText.body(card)
        text = f"{title}\n{body}".replace("**", "").strip()
        return text[:1800] or "PerpMirror 交易通知"

    @staticmethod
    def _channel_unavailable(message: str) -> bool:
        lowered = message.lower()
        return any(
            marker in lowered
            for marker in (
                "no such group",
                "invalid webhook",
                "signature",
                "sign match",
                "ip not allowed",
            )
        )

    async def close(self) -> None:
        await self._client.aclose()


class NotificationCardText:
    @staticmethod
    def title(card: dict[str, Any]) -> str:
        header = card.get("header")
        if not isinstance(header, dict):
            return "PerpMirror 交易通知"
        title = header.get("title")
        if not isinstance(title, dict):
            return "PerpMirror 交易通知"
        return str(title.get("content") or "PerpMirror 交易通知")

    @staticmethod
    def body(card: dict[str, Any]) -> str:
        body = card.get("body")
        if not isinstance(body, dict):
            return ""
        elements = body.get("elements")
        if not isinstance(elements, list):
            return ""
        return "\n".join(
            str(element.get("content", ""))
            for element in elements
            if isinstance(element, dict) and element.get("content")
        )
