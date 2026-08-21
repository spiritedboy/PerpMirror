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
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 19002, "msg": "no such group"})

    notifier = FeishuNotifier("https://example.test/webhook")
    await notifier._client.aclose()
    notifier._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match=r"19002.*no such group"):
        await notifier.send_card({"schema": "2.0"})
    await notifier.close()
