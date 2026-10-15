from datetime import date, time

import pytest

from tenderiq.dates import parse_portal_datetime


def test_tawreed_is_day_first_with_time():
    assert parse_portal_datetime("02/08/2026 15:00") == (date(2026, 8, 2), time(15, 0))


def test_pdo_is_day_first_date_only():
    assert parse_portal_datetime("18.08.2026") == (date(2026, 8, 18), None)


def test_blank_is_unknown():
    assert parse_portal_datetime("") == (None, None)
    assert parse_portal_datetime(None) == (None, None)


def test_unrecognised_format_fails_loudly():
    with pytest.raises(ValueError):
        parse_portal_datetime("Aug 2, 2026")
