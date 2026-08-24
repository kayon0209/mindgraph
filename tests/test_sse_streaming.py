from __future__ import annotations

import asyncio
import threading

from api.sse import iter_sync_events


def test_first_event_arrives_before_sync_producer_finishes() -> None:
    producer_finished = threading.Event()
    release_producer = threading.Event()

    def events():
        yield {"event": "answer_delta", "data": {"text": "first"}}
        release_producer.wait(timeout=2)
        producer_finished.set()
        yield {"event": "completed", "data": {}}

    async def scenario() -> None:
        stream = iter_sync_events(events)
        first = await asyncio.wait_for(anext(stream), timeout=1)

        assert first["data"]["text"] == "first"
        assert not producer_finished.is_set()

        release_producer.set()
        second = await asyncio.wait_for(anext(stream), timeout=1)
        assert second["event"] == "completed"
        await stream.aclose()

    asyncio.run(scenario())


def test_sync_producer_exception_is_raised_by_async_consumer() -> None:
    def events():
        yield {"event": "answer_delta", "data": {"text": "first"}}
        raise RuntimeError("provider failed")

    async def scenario() -> None:
        stream = iter_sync_events(events)
        assert (await anext(stream))["event"] == "answer_delta"
        try:
            await anext(stream)
        except RuntimeError as exc:
            assert str(exc) == "provider failed"
        else:
            raise AssertionError("producer exception was not propagated")

    asyncio.run(scenario())
