"""Natural Vietnamese queries for the deterministic local lunar calendar."""

from datetime import date
import re

from .lunar_calendar import (
    LunarDate,
    find_lunar_day,
    lunar_to_solar,
    solar_to_lunar,
)
from .text import normalize_text


_WEEKDAYS = (
    "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm",
    "Thứ Sáu", "Thứ Bảy", "Chủ Nhật",
)


def _format_solar(value):
    return f"{_WEEKDAYS[value.weekday()]}, ngày {value:%d/%m/%Y} dương lịch"


def _format_lunar(value):
    leap = " nhuận" if value.leap else ""
    return (
        f"ngày {value.day} tháng {value.month}{leap} "
        f"năm {value.year} âm lịch"
    )


def _parse_numeric_date(text):
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return False


def _parse_written_lunar_date(text, current_lunar):
    match = re.search(
        r"\bngay\s+(\d{1,2})\s+thang\s+(\d{1,2})"
        r"(?:\s+(?:nam\s+)?(\d{4}))?\s+(?:am lich|lich am)\b",
        text,
    )
    if not match:
        return None
    return LunarDate(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or current_lunar.year),
        "nhuan" in text,
    )


def _answer_lunar_to_solar(plain, today, current_lunar):
    lunar = _parse_written_lunar_date(plain, current_lunar)
    numeric = _parse_numeric_date(plain)
    wants_solar = any(term in plain for term in (
        "ngay duong", "duong lich", "sang duong", "doi sang duong",
    ))
    if lunar is None and numeric and wants_solar and "am lich" in plain:
        lunar = LunarDate(numeric.day, numeric.month, numeric.year, "nhuan" in plain)
    if lunar is None:
        return None
    converted = lunar_to_solar(lunar)
    if converted is None:
        return "ℹ️ Ngày âm lịch bạn nhập không tồn tại hoặc thông tin tháng nhuận chưa đúng."
    return f"🌙 {_format_lunar(lunar).capitalize()} là {_format_solar(converted)}."


def _answer_solar_to_lunar(plain):
    numeric = _parse_numeric_date(plain)
    if numeric is False:
        return "ℹ️ Ngày dương lịch bạn nhập không hợp lệ."
    if numeric is None:
        return None
    wants_lunar = any(term in plain for term in (
        "ngay am", "am lich", "lich am", "sang am", "doi sang am",
    ))
    wants_solar = any(term in plain for term in ("ngay duong", "duong lich"))
    if not wants_lunar or wants_solar:
        return None
    lunar = solar_to_lunar(numeric)
    return f"🌙 {_format_solar(numeric).capitalize()} là {_format_lunar(lunar)}."


def _answer_monthly_observance(plain, today, current_lunar, lunar_day, label, icon):
    requested = re.search(
        r"\bthang\s+(\d{1,2})(?:\s+(?:nam\s+)?(\d{4}))?\b", plain
    )
    if "thang nay" in plain:
        wanted = LunarDate(
            lunar_day, current_lunar.month, current_lunar.year, current_lunar.leap
        )
        converted = lunar_to_solar(wanted)
    elif requested:
        month = int(requested.group(1))
        year = int(requested.group(2) or current_lunar.year)
        wanted = LunarDate(lunar_day, month, year)
        converted = lunar_to_solar(wanted)
        if converted is not None and converted < today and requested.group(2) is None:
            wanted = LunarDate(lunar_day, month, year + 1)
            converted = lunar_to_solar(wanted)
    else:
        converted = find_lunar_day(today, lunar_day)
        wanted = solar_to_lunar(converted) if converted else None
    if converted is None or wanted is None:
        return f"ℹ️ Không xác định được {label.lower()} theo tháng âm lịch bạn yêu cầu."
    timing = "hôm nay" if converted == today else _format_solar(converted)
    return f"{icon} {label} ({_format_lunar(wanted)}) rơi vào {timing}."


def answer_lunar_calendar(command, *, today=None):
    """Return a deterministic answer, or ``None`` for unrelated commands."""
    plain = normalize_text(command)
    lunar_terms = ("am lich", "lich am", "ram", "mung mot", "mung 1")
    if not any(term in plain for term in lunar_terms):
        return None

    today = today or date.today()
    current_lunar = solar_to_lunar(today)

    converted = _answer_lunar_to_solar(plain, today, current_lunar)
    if converted is not None:
        return converted
    converted = _answer_solar_to_lunar(plain)
    if converted is not None:
        return converted

    if "ram" in plain:
        return _answer_monthly_observance(
            plain, today, current_lunar, 15, "Rằm", "🌕"
        )
    if "mung mot" in plain or "mung 1" in plain:
        return _answer_monthly_observance(
            plain, today, current_lunar, 1, "Mùng một", "🌑"
        )

    if any(term in plain for term in (
        "hom nay", "ngay may", "ngay gi", "bay gio", "hien tai",
    )) or plain in {"am lich", "lich am", "xem am lich", "xem lich am"}:
        return (
            f"🌙 Hôm nay là {_format_solar(today)}; "
            f"tương ứng {_format_lunar(current_lunar)}."
        )

    return (
        "ℹ️ Bạn có thể hỏi `hôm nay là ngày mấy âm lịch`, `rằm tháng này`, "
        "`mùng 1 sắp tới` hoặc `15/08/2026 là ngày âm lịch nào`."
    )
