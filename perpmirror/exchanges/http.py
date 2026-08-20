from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from perpmirror.exceptions import NonRetryableExchangeError, RetryableExchangeError

logger = logging.getLogger(__name__)


class ReliableHttpClient:
    def __init__(self, base_url: str, *, retries: int = 3, timeout_seconds: float = 10.0) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self.retries = retries

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        content: str | bytes | None = None,
        headers: Mapping[str, str] | None = None,
        retry: bool = True,
    ) -> httpx.Response:
        attempts = self.retries if retry else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self.client.request(
                    method, path, params=params, content=content, headers=headers
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise RetryableExchangeError(f"network failure during {method} {path}") from exc
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = RetryableExchangeError(
                        f"retryable HTTP status {response.status_code} during {method} {path}"
                    )
                    if attempt + 1 >= attempts:
                        raise last_error
                elif response.is_error:
                    raise NonRetryableExchangeError(
                        f"exchange HTTP {response.status_code} during {method} {path}"
                    )
                else:
                    return response
            delay = min(0.5 * (2**attempt), 4.0)
            logger.warning(
                "HTTP_RETRY method=%s path=%s attempt=%s delay=%s", method, path, attempt + 1, delay
            )
            await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    async def close(self) -> None:
        await self.client.aclose()
