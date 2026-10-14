from datetime import datetime, timezone

import pytest

from fbdb.ops.schedule import next_daily, next_weekly, parse_day, parse_hhmm

UTC = timezone.utc


def test_next_weekly_later_same_day():
    now = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)          # Monday 02:00
    assert next_weekly(now, 0, 3, 0) == datetime(2026, 10, 12, 3, 0, tzinfo=UTC)


def test_next_weekly_after_time_goes_to_next_week():
    now = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)          # Monday exactly 03:00 -> strictly after
    assert next_weekly(now, 0, 3, 0) == datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


def test_next_weekly_other_weekday():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)         # Wednesday
    assert next_weekly(now, 0, 3, 0) == datetime(2026, 10, 19, 3, 0, tzinfo=UTC)
    assert next_weekly(now, 4, 23, 30) == datetime(2026, 10, 16, 23, 30, tzinfo=UTC)


def test_next_daily():
    assert next_daily(datetime(2026, 10, 14, 9, 0, tzinfo=UTC), 9, 30) == datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    assert next_daily(datetime(2026, 10, 14, 9, 30, tzinfo=UTC), 9, 30) == datetime(2026, 10, 15, 9, 30, tzinfo=UTC)


def test_month_boundary():
    now = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert next_daily(now, 9, 30) == datetime(2027, 1, 1, 9, 30, tzinfo=UTC)


def test_parsers():
    assert parse_hhmm("03:00") == (3, 0)
    assert parse_day("Monday") == 0 and parse_day("sun") == 6
    for bad in ("3", "25:00", "10:61", "ab:cd"):
        with pytest.raises(ValueError):
            parse_hhmm(bad)
    with pytest.raises(ValueError):
        parse_day("someday")
