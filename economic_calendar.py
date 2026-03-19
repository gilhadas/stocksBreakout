"""
economic_calendar.py
====================
Lightweight economic event calendar for position-sizing adjustments.

FOMC dates are published a year in advance by the Federal Reserve.
CPI is released ~2nd-3rd week of each month; NFP is the first Friday.

Usage:
    from economic_calendar import get_event_context
    ctx = get_event_context()       # checks today
    ctx = get_event_context(date(2026, 3, 18))  # check specific date
"""

from datetime import date, timedelta
from config import ECONOMIC_CALENDAR

# ── FOMC decision dates (2026) ───────────────────────────────────────────────
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Each entry is the *announcement* day (usually Wednesday of a 2-day meeting).
FOMC_DATES_2026 = {
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 5, 6),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 11, 4),
    date(2026, 12, 16),
}

# ── CPI release dates (2026, approximate) ────────────────────────────────────
# BLS publishes ~12th-14th of each month for the prior month.
CPI_DATES_2026 = {
    date(2026, 1, 14), date(2026, 2, 11), date(2026, 3, 11),
    date(2026, 4, 14), date(2026, 5, 13), date(2026, 6, 10),
    date(2026, 7, 14), date(2026, 8, 12), date(2026, 9, 15),
    date(2026, 10, 13), date(2026, 11, 12), date(2026, 12, 10),
}


def _first_friday(year: int, month: int) -> date:
    """Return the first Friday of the given month."""
    d = date(year, month, 1)
    # weekday(): Mon=0 .. Fri=4
    days_until_fri = (4 - d.weekday()) % 7
    return d + timedelta(days=days_until_fri)


# NFP: first Friday of each month
NFP_DATES_2026 = {_first_friday(2026, m) for m in range(1, 13)}


def is_fomc_day(d: date = None) -> bool:
    d = d or date.today()
    return d in FOMC_DATES_2026


def is_cpi_day(d: date = None) -> bool:
    d = d or date.today()
    return d in CPI_DATES_2026


def is_nfp_day(d: date = None) -> bool:
    d = d or date.today()
    return d in NFP_DATES_2026


def get_event_context(d: date = None) -> dict:
    """
    Check if the given date is a high-volatility economic event day.

    Returns:
        {
            'is_event_day': bool,
            'event_name': str,          # '' if no event
            'sizing_mult': float,       # 1.0 = normal, <1.0 = reduced
        }
    """
    if not ECONOMIC_CALENDAR.get('enabled', True):
        return {'is_event_day': False, 'event_name': '', 'sizing_mult': 1.0}

    d = d or date.today()

    if is_fomc_day(d):
        return {
            'is_event_day': True,
            'event_name': 'FOMC',
            'sizing_mult': ECONOMIC_CALENDAR.get('fomc_sizing_mult', 0.50),
        }
    if is_cpi_day(d):
        return {
            'is_event_day': True,
            'event_name': 'CPI',
            'sizing_mult': ECONOMIC_CALENDAR.get('cpi_sizing_mult', 0.75),
        }
    if is_nfp_day(d):
        return {
            'is_event_day': True,
            'event_name': 'NFP',
            'sizing_mult': ECONOMIC_CALENDAR.get('nfp_sizing_mult', 0.75),
        }

    return {'is_event_day': False, 'event_name': '', 'sizing_mult': 1.0}
