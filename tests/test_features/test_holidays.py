"""Tests for src/features/holidays.py."""

from datetime import UTC, date, datetime

from src.features.holidays import is_holiday


class TestIsHoliday:
    def test_new_year_2024(self) -> None:
        assert is_holiday(date(2024, 1, 1)) is True

    def test_epiphany_2024(self) -> None:
        assert is_holiday(date(2024, 1, 6)) is True

    def test_non_holiday_monday(self) -> None:
        # 2024-01-15 is a regular Monday
        assert is_holiday(date(2024, 1, 15)) is False

    def test_viernes_santo_2025(self) -> None:
        # Easter Friday 2025 = April 18
        assert is_holiday(date(2025, 4, 18)) is True

    def test_jueves_santo_2025(self) -> None:
        # Easter Thursday 2025 = April 17
        assert is_holiday(date(2025, 4, 17)) is True

    def test_comunidad_madrid_may_2(self) -> None:
        assert is_holiday(date(2024, 5, 2)) is True
        assert is_holiday(date(2025, 5, 2)) is True
        assert is_holiday(date(2026, 5, 2)) is True

    def test_almudena_nov_9(self) -> None:
        # Madrid city holiday — added manually
        assert is_holiday(date(2024, 11, 9)) is True
        assert is_holiday(date(2025, 11, 9)) is True
        assert is_holiday(date(2026, 11, 9)) is True

    def test_christmas_2026(self) -> None:
        assert is_holiday(date(2026, 12, 25)) is True

    def test_labour_day(self) -> None:
        assert is_holiday(date(2024, 5, 1)) is True

    def test_accepts_datetime(self) -> None:
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        assert is_holiday(dt) is True

    def test_accepts_datetime_non_holiday(self) -> None:
        dt = datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
        assert is_holiday(dt) is False

    def test_regular_weekday_is_false(self) -> None:
        # A random Tuesday in March
        assert is_holiday(date(2024, 3, 12)) is False
