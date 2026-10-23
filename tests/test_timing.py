"""Self-check: the cadence tolerances accept today's schedule and a coordinator's.

No coordinator exists yet, so the timelines below are synthetic: the fetch
times each scheduling model would produce for a device that is always polled.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from .timing import assert_fetch_cadence, group_rounds

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
JITTER = timedelta(microseconds=500_000)  # coordinators offset by < 1 s


def _today(scan: int, end: int) -> list[datetime]:
    """Entity polls every 30 s; fetch once now - (last fetch - 5 s) >= scan."""
    times, last_update = [], None
    for t in range(30, end + 1, 30):
        if last_update is None or t - last_update >= scan:
            times.append(T0 + timedelta(seconds=t))
            last_update = t - 5
    return times


def _coordinator(scan: int, end: int) -> list[datetime]:
    """First refresh during setup, then every update_interval."""
    return [T0 + timedelta(seconds=t) + JITTER for t in range(0, end + 1, scan)]


@pytest.mark.parametrize("scan", [60, 100, 180, 300])
@pytest.mark.parametrize("schedule", [_today, _coordinator])
def test_tolerances_accept_both_schedules(schedule, scan: int) -> None:
    end = scan * 10
    rounds = group_rounds(schedule(scan, end))
    assert_fetch_cadence(
        rounds, T0, T0 + timedelta(seconds=end), timedelta(seconds=scan)
    )


def test_rejects_fetching_too_often() -> None:
    rounds = group_rounds([T0, T0 + timedelta(seconds=60)])
    with pytest.raises(AssertionError, match="apart"):
        assert_fetch_cadence(rounds, T0, T0 + timedelta(seconds=60), timedelta(seconds=180))


def test_rejects_missing_fetch() -> None:
    rounds = group_rounds([T0, T0 + timedelta(seconds=400)])
    with pytest.raises(AssertionError, match="no fetch"):
        assert_fetch_cadence(rounds, T0, T0 + timedelta(seconds=400), timedelta(seconds=180))


def test_rejects_late_first_fetch() -> None:
    rounds = group_rounds([T0 + timedelta(seconds=60)])
    with pytest.raises(AssertionError, match="first fetch"):
        assert_fetch_cadence(rounds, T0, T0 + timedelta(seconds=60), timedelta(seconds=180))


def test_retries_are_one_round() -> None:
    rounds = group_rounds([T0, T0, T0 + timedelta(seconds=3), T0 + timedelta(seconds=180)])
    assert [len(r) for r in rounds] == [3, 1]
