import json

import httpx
import pytest

from perpmirror.notifications.feishu import FeishuNotifier


@pytest.mark.asyncio
async def test_permanent_feishu_error_includes_response_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="no such group")

    notifier = FeishuNotifier("https://example.test/webhook")
    await notifier._client.aclose()
    notifier._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match=r"HTTP 400.*no such group"):
        await notifier.send_card({"schema": "2.0"})
    await notifier.close()


@pytest.mark.asyncio
async def test_feishu_json_error_includes_message() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": 19002, "msg": "no such group"})

    notifier = FeishuNotifier("https://example.test/webhook")
    await notifier._client.aclose()
    notifier._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match=r"19002.*no such group"):
        await notifier.send_card({"schema": "2.0"})
    assert calls == 1
    await notifier.close()


@pytest.mark.asyncio
async def test_rejected_interactive_card_falls_back_to_plain_text() -> None:
    message_types: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        message_types.append(payload["msg_type"])
        if payload["msg_type"] == "interactive":
            return httpx.Response(200, json={"code": 230001, "msg": "invalid card data"})
        assert "开仓 · BTC-USDT-PERP" in payload["content"]["text"]
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    notifier = FeishuNotifier("https://example.test/webhook")
    await notifier._client.aclose()
    notifier._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await notifier.send_card(
        {
            "header": {"title": {"content": "🟢 开仓 · BTC-USDT-PERP"}},
            "body": {"elements": [{"content": "okx_fixed · 0 U → 400 U"}]},
        }
    )
    assert message_types == ["interactive", "text"]
    await notifier.close()
