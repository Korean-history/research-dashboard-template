"""Small date/range parser for card chronology diagnostics."""
from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import asdict, dataclass
from typing import Any

SEASON_RANGES = {
    "spring": ((3, 1), (5, 31)),
    "summer": ((6, 1), (8, 31)),
    "autumn": ((9, 1), (11, 30)),
    "fall": ((9, 1), (11, 30)),
}


@dataclass(frozen=True)
class DateInterval:
    literal: str
    start: dt.date
    end: dt.date
    precision: str

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        return data


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value).strip()


def _parse_single(raw: str) -> DateInterval:
    text = re.sub(r"\s+", " ", raw.strip())
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return DateInterval(text, dt.date(year, 1, 1), dt.date(year, 12, 31), "year")
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = (int(part) for part in text.split("-"))
        last_day = calendar.monthrange(year, month)[1]
        return DateInterval(text, dt.date(year, month, 1), dt.date(year, month, last_day), "month")
    if re.fullmatch(r"\d{4}/\d{1,2}", text):
        year_text, month_text = text.split("/")
        year = int(year_text)
        month = int(month_text)
        last_day = calendar.monthrange(year, month)[1]
        return DateInterval(text, dt.date(year, month, 1), dt.date(year, month, last_day), "month")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = dt.date.fromisoformat(text)
        return DateInterval(text, day, day, "day")
    season_match = re.fullmatch(r"(?i)(spring|summer|autumn|fall)\s+(\d{4})", text)
    if season_match:
        season = season_match.group(1).lower()
        year = int(season_match.group(2))
        (start_month, start_day), (end_month, end_day) = SEASON_RANGES[season]
        return DateInterval(text, dt.date(year, start_month, start_day), dt.date(year, end_month, end_day), "season")
    fiscal_match = re.fullmatch(r"(?i)(?:fy|fiscal year)\s*(\d{4})", text)
    if fiscal_match:
        year = int(fiscal_match.group(1))
        return DateInterval(text, dt.date(year, 4, 1), dt.date(year + 1, 3, 31), "fiscal_year")
    raise ValueError(f"unsupported date_or_range literal: {raw!r}")


def _parse_range(text: str) -> DateInterval | None:
    for pattern in [
        r"^(\d{4})\s*-\s*(\d{4})$",
        r"^(.+?)\s*\.\.\s*(.+)$",
        r"^(.+?)\s*/\s*(.+)$",
    ]:
        match = re.fullmatch(pattern, text)
        if not match:
            continue
        left, right = match.group(1).strip(), match.group(2).strip()
        start = _parse_single(left)
        end = _parse_single(right)
        if start.start > end.end:
            raise ValueError(f"date range start after end: {text!r}")
        return DateInterval(text, start.start, end.end, "range")
    return None


def parse_date_or_range(value: Any) -> DateInterval | None:
    text = as_text(value)
    if not text:
        return None
    try:
        return _parse_single(text)
    except ValueError as single_error:
        parsed_range = _parse_range(text)
        if parsed_range:
            return parsed_range
        raise single_error


def strictly_after(left: DateInterval | None, right: DateInterval | None) -> bool:
    return bool(left and right and left.start > right.end)


def overlaps(left: DateInterval | None, right: DateInterval | None) -> bool:
    return bool(left and right and left.start <= right.end and left.end >= right.start)


def precision_rank(interval: DateInterval | None) -> int:
    if interval is None:
        return 0
    return {"range": 0, "year": 1, "fiscal_year": 1, "season": 2, "month": 2, "day": 3}.get(interval.precision, 0)
