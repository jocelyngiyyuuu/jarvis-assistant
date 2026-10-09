"""Deterministic Vietnamese lunar calendar conversion for local Jarvis use.

The astronomical algorithm follows the commonly used Ho Ngoc Duc conversion
method and uses Vietnam's UTC+7 time zone by default.
"""

from dataclasses import dataclass
from datetime import date, timedelta
import math


VIETNAM_TIMEZONE = 7.0
_SYNODIC_MONTH = 29.530588853
_NEW_MOON_EPOCH = 2415021.076998695


@dataclass(frozen=True)
class LunarDate:
    day: int
    month: int
    year: int
    leap: bool = False


def _jd_from_date(day, month, year):
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    jd = day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    if jd < 2299161:
        jd = day + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083
    return jd


def _jd_to_date(jd):
    if jd > 2299160:
        a = jd + 32044
        b = (4 * a + 3) // 146097
        c = a - (b * 146097) // 4
    else:
        b = 0
        c = jd + 32082
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = b * 100 + d - 4800 + m // 10
    return date(year, month, day)


def _new_moon(k):
    t = k / 1236.85
    t2 = t * t
    t3 = t2 * t
    radians = math.pi / 180
    jd1 = (
        2415020.75933 + _SYNODIC_MONTH * k
        + 0.0001178 * t2 - 0.000000155 * t3
        + 0.00033 * math.sin((166.56 + 132.87 * t - 0.009173 * t2) * radians)
    )
    m = 359.2242 + 29.10535608 * k - 0.0000333 * t2 - 0.00000347 * t3
    m_prime = 306.0253 + 385.81691806 * k + 0.0107306 * t2 + 0.00001236 * t3
    f = 21.2964 + 390.67050646 * k - 0.0016528 * t2 - 0.00000239 * t3
    correction = (
        (0.1734 - 0.000393 * t) * math.sin(m * radians)
        + 0.0021 * math.sin(2 * m * radians)
        - 0.4068 * math.sin(m_prime * radians)
        + 0.0161 * math.sin(2 * m_prime * radians)
        - 0.0004 * math.sin(3 * m_prime * radians)
        + 0.0104 * math.sin(2 * f * radians)
        - 0.0051 * math.sin((m + m_prime) * radians)
        - 0.0074 * math.sin((m - m_prime) * radians)
        + 0.0004 * math.sin((2 * f + m) * radians)
        - 0.0004 * math.sin((2 * f - m) * radians)
        - 0.0006 * math.sin((2 * f + m_prime) * radians)
        + 0.0010 * math.sin((2 * f - m_prime) * radians)
        + 0.0005 * math.sin((2 * m_prime + m) * radians)
    )
    if t < -11:
        delta_t = (
            0.001 + 0.000839 * t + 0.0002261 * t2
            - 0.00000845 * t3 - 0.000000081 * t * t3
        )
    else:
        delta_t = -0.000278 + 0.000265 * t + 0.000262 * t2
    return jd1 + correction - delta_t


def _new_moon_day(k, timezone):
    return int(_new_moon(k) + 0.5 + timezone / 24)


def _sun_longitude(jdn, timezone):
    t = (jdn - 2451545.5 - timezone / 24) / 36525
    t2 = t * t
    radians = math.pi / 180
    m = 357.52910 + 35999.05030 * t - 0.0001559 * t2 - 0.00000048 * t * t2
    l0 = 280.46645 + 36000.76983 * t + 0.0003032 * t2
    delta = (
        (1.914600 - 0.004817 * t - 0.000014 * t2) * math.sin(radians * m)
        + (0.019993 - 0.000101 * t) * math.sin(2 * radians * m)
        + 0.000290 * math.sin(3 * radians * m)
    )
    longitude = (l0 + delta) * radians
    longitude -= math.pi * 2 * int(longitude / (math.pi * 2))
    return int(longitude / math.pi * 6)


def _lunar_month_11(year, timezone):
    offset = _jd_from_date(31, 12, year) - 2415021
    k = int(offset / _SYNODIC_MONTH)
    new_moon = _new_moon_day(k, timezone)
    if _sun_longitude(new_moon, timezone) >= 9:
        new_moon = _new_moon_day(k - 1, timezone)
    return new_moon


def _leap_month_offset(month_11, timezone):
    k = int(0.5 + (month_11 - _NEW_MOON_EPOCH) / _SYNODIC_MONTH)
    last = 0
    index = 1
    arc = _sun_longitude(_new_moon_day(k + index, timezone), timezone)
    while True:
        last = arc
        index += 1
        arc = _sun_longitude(_new_moon_day(k + index, timezone), timezone)
        if arc == last or index >= 14:
            break
    return index - 1


def solar_to_lunar(value, timezone=VIETNAM_TIMEZONE):
    """Convert a Gregorian date to a Vietnamese lunar date."""
    day_number = _jd_from_date(value.day, value.month, value.year)
    k = int((day_number - _NEW_MOON_EPOCH) / _SYNODIC_MONTH)
    month_start = _new_moon_day(k + 1, timezone)
    if month_start > day_number:
        month_start = _new_moon_day(k, timezone)
    month_11 = _lunar_month_11(value.year, timezone)
    next_month_11 = month_11
    if month_11 >= month_start:
        lunar_year = value.year
        month_11 = _lunar_month_11(value.year - 1, timezone)
    else:
        lunar_year = value.year + 1
        next_month_11 = _lunar_month_11(value.year + 1, timezone)
    lunar_day = day_number - month_start + 1
    difference = int((month_start - month_11) / 29)
    lunar_month = difference + 11
    lunar_leap = False
    if next_month_11 - month_11 > 365:
        leap_difference = _leap_month_offset(month_11, timezone)
        if difference >= leap_difference:
            lunar_month = difference + 10
            if difference == leap_difference:
                lunar_leap = True
    if lunar_month > 12:
        lunar_month -= 12
    if lunar_month >= 11 and difference < 4:
        lunar_year -= 1
    return LunarDate(lunar_day, lunar_month, lunar_year, lunar_leap)


def lunar_to_solar(value, timezone=VIETNAM_TIMEZONE):
    """Convert a Vietnamese lunar date to Gregorian, or return ``None``."""
    if not 1 <= value.month <= 12 or not 1 <= value.day <= 30:
        return None
    if value.month < 11:
        month_11 = _lunar_month_11(value.year - 1, timezone)
        next_month_11 = _lunar_month_11(value.year, timezone)
    else:
        month_11 = _lunar_month_11(value.year, timezone)
        next_month_11 = _lunar_month_11(value.year + 1, timezone)
    k = int(0.5 + (month_11 - _NEW_MOON_EPOCH) / _SYNODIC_MONTH)
    offset = value.month - 11
    if offset < 0:
        offset += 12
    if next_month_11 - month_11 > 365:
        leap_offset = _leap_month_offset(month_11, timezone)
        leap_month = leap_offset - 2
        if leap_month < 0:
            leap_month += 12
        if value.leap and value.month != leap_month:
            return None
        if value.leap or offset >= leap_offset:
            offset += 1
    elif value.leap:
        return None
    month_start = _new_moon_day(k + offset, timezone)
    converted = _jd_to_date(month_start + value.day - 1)
    # Reject day 30 in lunar months that only have 29 days.
    return converted if solar_to_lunar(converted, timezone) == value else None


def find_lunar_day(start, lunar_day, *, include_start=True, limit=60):
    """Find the next Gregorian date whose lunar day equals ``lunar_day``."""
    if not 1 <= lunar_day <= 30:
        return None
    current = start if include_start else start + timedelta(days=1)
    for offset in range(max(0, int(limit)) + 1):
        candidate = current + timedelta(days=offset)
        if solar_to_lunar(candidate).day == lunar_day:
            return candidate
    return None
