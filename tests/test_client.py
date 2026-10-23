"""Characterization tests for LuftdatenClient (fetch gating, retries, parsing).

Every test here pins LuftdatenClient internals (throttling, the 5 s lastUpdate
correction, retry counts, freshness bookkeeping) and is marked
implementation_detail. The user-visible effects are covered by the unmarked
tests in test_sensor.py.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.local_luftdaten.sensor import (
    REQUEST_RETRIES,
    REQUEST_RETRY_DELAY,
    STALE_AFTER_INTERVALS,
    LuftdatenClient,
    LuftdatenError,
)

from .conftest import load_fixture_json, load_fixture_text

pytestmark = pytest.mark.implementation_detail

RESOURCE = "http://192.168.0.123/data.json"
SCAN_INTERVAL = timedelta(seconds=180)
VALID = load_fixture_text("sds011_bme280.json")


class FakeResponse:
    """Minimal stand-in for aiohttp.ClientResponse."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def text(self) -> str:
        return self._text


class FakeSession:
    """Session returning queued results; exceptions in the queue are raised."""

    def __init__(self, *results: str | BaseException) -> None:
        self._results = list(results)
        self.requests: list[str] = []
        # When set, get() blocks until the event fires (for concurrency tests).
        self.gate: asyncio.Event | None = None

    def queue(self, *results: str | BaseException) -> None:
        self._results.extend(results)

    async def get(self, url: str) -> FakeResponse:
        self.requests.append(url)
        if self.gate is not None:
            await self.gate.wait()
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return FakeResponse(result)


@pytest.fixture
def mock_sleep():
    """Skip the real retry delay.

    The client calls asyncio.sleep through the asyncio module, so this patches
    it globally for the duration of the test.
    """
    with patch(
        "custom_components.local_luftdaten.sensor.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        yield sleep


def make_client(session: FakeSession) -> LuftdatenClient:
    return LuftdatenClient(session, RESOURCE, SCAN_INTERVAL)


async def test_initial_state() -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    A fresh client has no data and is not fresh.
    """
    client = make_client(FakeSession())
    assert client.data is None
    assert client.lastSuccess is None
    assert client.is_data_fresh is False


async def test_first_call_fetches_immediately(freezer: FrozenDateTimeFactory) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    The first update fetches without waiting for scan_interval.
    """
    session = FakeSession(VALID)
    client = make_client(session)

    await client.async_update()

    assert session.requests == [RESOURCE]
    assert client.data == load_fixture_json("sds011_bme280.json")
    assert client.is_data_fresh is True


async def test_second_call_within_interval_does_not_fetch(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    A call inside scan_interval returns without a request.
    """
    session = FakeSession(VALID)
    client = make_client(session)
    await client.async_update()

    freezer.tick(timedelta(seconds=60))
    await client.async_update()

    assert len(session.requests) == 1


async def test_refetch_after_interval_with_5s_correction(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    lastUpdate is stored 5 s in the past, so the next fetch is due after
    scan_interval - 5 s, not after the full scan_interval."""
    session = FakeSession(VALID, VALID)
    client = make_client(session)
    await client.async_update()

    freezer.tick(SCAN_INTERVAL - timedelta(seconds=5) - timedelta(milliseconds=1))
    await client.async_update()
    assert len(session.requests) == 1

    freezer.tick(timedelta(milliseconds=1))
    await client.async_update()
    assert len(session.requests) == 2


async def test_single_timeout_then_success(
    freezer: FrozenDateTimeFactory, mock_sleep: AsyncMock
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    One timeout is retried after REQUEST_RETRY_DELAY and then succeeds.
    """
    session = FakeSession(asyncio.TimeoutError(), VALID)
    client = make_client(session)

    await client.async_update()

    assert len(session.requests) == 2
    mock_sleep.assert_awaited_once_with(REQUEST_RETRY_DELAY)
    assert client.is_data_fresh is True


async def test_all_attempts_fail_keeps_old_data(
    freezer: FrozenDateTimeFactory, mock_sleep: AsyncMock
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    After 1 + REQUEST_RETRIES failures LuftdatenError is raised, previous
    data is kept, and the next attempt waits a full scan_interval."""
    session = FakeSession(VALID)
    client = make_client(session)
    await client.async_update()
    old_data = client.data
    old_success = client.lastSuccess

    freezer.tick(SCAN_INTERVAL)
    session.queue(
        aiohttp.ClientError("boom"),
        asyncio.TimeoutError(),
        aiohttp.ClientConnectionError("down"),
    )
    with pytest.raises(LuftdatenError):
        await client.async_update()

    assert REQUEST_RETRIES == 2
    assert len(session.requests) == 1 + 3
    assert mock_sleep.await_count == REQUEST_RETRIES
    assert client.data is old_data
    assert client.lastSuccess == old_success
    assert client.is_data_fresh is True

    # Characterization: current behavior, possibly unintended
    # A call right after the failure is gated silently: no request and no
    # LuftdatenError, so callers cannot tell "skipped" from "succeeded".
    await client.async_update()
    assert len(session.requests) == 4

    freezer.tick(SCAN_INTERVAL - timedelta(seconds=5) - timedelta(milliseconds=1))
    await client.async_update()
    assert len(session.requests) == 4

    session.queue(VALID)
    freezer.tick(timedelta(milliseconds=1))
    await client.async_update()
    assert len(session.requests) == 5


async def test_unexpected_exception_propagates(freezer: FrozenDateTimeFactory) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    Only aiohttp.ClientError and TimeoutError are retried.
    """
    session = FakeSession(ValueError("unexpected"))
    client = make_client(session)

    with pytest.raises(ValueError):
        await client.async_update()

    assert len(session.requests) == 1


@pytest.mark.parametrize("payload", ["this is not json", "[1, 2, 3]", '"text"', "null"])
async def test_invalid_or_non_dict_json_clears_data(
    freezer: FrozenDateTimeFactory, payload: str
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    Unparseable or non-object JSON sets data=None without raising.
    """
    session = FakeSession(VALID, payload)
    client = make_client(session)
    await client.async_update()
    assert client.is_data_fresh is True

    freezer.tick(SCAN_INTERVAL)
    await client.async_update()

    # Characterization: current behavior, possibly unintended
    # Unlike a network failure (which keeps data for STALE_AFTER_INTERVALS),
    # one bad payload discards the previous data and makes the client stale
    # immediately, even though lastSuccess is still recent.
    assert client.data is None
    assert client.lastSuccess is not None
    assert client.is_data_fresh is False


async def test_dict_without_sensordatavalues_is_accepted(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    Any JSON object counts as success; the shape is not validated.
    """
    # Characterization: current behavior, possibly unintended
    session = FakeSession("{}")
    client = make_client(session)

    await client.async_update()

    assert client.data == {}
    assert client.is_data_fresh is True


async def test_is_data_fresh_boundary(freezer: FrozenDateTimeFactory) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    Data is fresh while younger than STALE_AFTER_INTERVALS * scan_interval.
    """
    assert STALE_AFTER_INTERVALS == 3
    session = FakeSession(VALID)
    client = make_client(session)
    await client.async_update()

    freezer.tick(SCAN_INTERVAL * 3 - timedelta(milliseconds=1))
    assert client.is_data_fresh is True

    freezer.tick(timedelta(milliseconds=1))
    assert client.is_data_fresh is False


async def test_parallel_calls_make_one_request(freezer: FrozenDateTimeFactory) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    Concurrent updates are serialized by the lock; only one fetches.
    """
    session = FakeSession(VALID)
    session.gate = asyncio.Event()
    client = make_client(session)

    tasks = [asyncio.create_task(client.async_update()) for _ in range(5)]
    await asyncio.sleep(0)
    session.gate.set()
    await asyncio.gather(*tasks)

    assert session.requests == [RESOURCE]
    assert client.is_data_fresh is True
