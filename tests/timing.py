"""Time-based helpers for refactoring-proof integration tests.

The assertions here describe what a user (or the device) can observe and are
deliberately tolerant of *how* updates are scheduled. They must hold both for
today's implementation (HA polls entities every 30 s, the client throttles to
scan_interval) and for a DataUpdateCoordinator with
update_interval=scan_interval.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

# HA's default entity polling interval for sensors. Only the tolerances below
# depend on it; no test asserts that polling happens on this grid.
HA_POLL = timedelta(seconds=30)

# The first fetch may happen during setup (coordinator first refresh) or on
# the first entity poll (today).
FIRST_FETCH_WITHIN = HA_POLL
# Two fetches may be slightly closer than scan_interval (today the client
# subtracts 5 s from lastUpdate to absorb call-time jitter).
EARLY_TOLERANCE = timedelta(seconds=5)
# A due fetch may be delayed until the next entity poll.
LATE_TOLERANCE = HA_POLL
# Requests closer together than this belong to one fetch (retries).
RETRY_WINDOW = timedelta(seconds=30)

STEP = timedelta(seconds=5)


class FakeDevice:
    """Fake data.json endpoint that records when it was requested."""

    def __init__(self, aioclient_mock: AiohttpClientMocker, url: str) -> None:
        self.url = url
        self.request_times: list[datetime] = []
        self._response: dict[str, Any] = {}
        aioclient_mock.get(url, side_effect=self._handle)

    def respond(self, *, text: str | None = None, exc: Exception | None = None):
        self._response = {"text": text, "exc": exc}

    async def _handle(self, method, url, data):
        self.request_times.append(dt_util.utcnow())
        return AiohttpClientMockResponse(method, url, **self._response)

    def rounds(self, since: datetime | None = None) -> list[list[datetime]]:
        """Requests grouped into fetches (a fetch plus its retries)."""
        return group_rounds(
            [t for t in self.request_times if since is None or t >= since]
        )


def group_rounds(times: list[datetime]) -> list[list[datetime]]:
    rounds: list[list[datetime]] = []
    for t in sorted(times):
        if rounds and t - rounds[-1][0] < RETRY_WINDOW:
            rounds[-1].append(t)
        else:
            rounds.append([t])
    return rounds


def assert_fetch_cadence(
    rounds: list[list[datetime]],
    start: datetime,
    end: datetime,
    scan_interval: timedelta,
) -> None:
    """At most one fetch per scan_interval, and a new one at the latest after
    scan_interval (+ tolerance), from start until end."""
    assert rounds, "no request at all"
    starts = [r[0] for r in rounds]
    assert starts[0] - start <= FIRST_FETCH_WITHIN, "first fetch too late"
    for prev, cur in zip(starts, starts[1:]):
        gap = cur - prev
        assert gap >= scan_interval - EARLY_TOLERANCE, f"fetches only {gap} apart"
        assert gap <= scan_interval + LATE_TOLERANCE, f"no fetch for {gap}"
    assert end - starts[-1] <= scan_interval + LATE_TOLERANCE, "fetching stopped"


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time forward in small steps, letting any scheduler fire."""
    remaining = delta
    while remaining > timedelta(0):
        step = min(STEP, remaining)
        freezer.tick(step)
        async_fire_time_changed(hass)
        # Entity polling runs as a background task.
        await hass.async_block_till_done(wait_background_tasks=True)
        remaining -= step


async def advance_to(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, target: datetime
) -> None:
    """Move time forward until `target` (no-op if already past it)."""
    await advance(hass, freezer, target - dt_util.utcnow())
