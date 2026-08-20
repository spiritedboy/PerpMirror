import asyncio

import pytest

from perpmirror.monitoring.coalescer import ReconcileCoalescer


@pytest.mark.asyncio
async def test_event_during_reconcile_creates_one_pending_run() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def callback() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()

    coalescer = ReconcileCoalescer(callback, 0)
    coalescer.start()
    await coalescer.trigger()
    await started.wait()
    for _ in range(5):
        await coalescer.trigger()
    release.set()
    await asyncio.sleep(0.02)
    await coalescer.stop()
    assert calls == 2
