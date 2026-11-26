"""5-field cron: validate, describe (vi), next fire times.

The UI never re-implements these rules (P07) — this module is the authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIN_GAP_SECONDS = 5 * 60
_LOOKAHEAD = timedelta(days=400)
_WEEKDAYS = ["Chủ nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"]


class CronError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class CronValidation:
    valid: bool
    message: str | None = None
    code: str | None = None
    cron_description: str | None = None
    next_5_runs_local: list[str] | None = None
    next_run_at: str | None = None


def _norm_dow(n: int) -> int:
    return 0 if n == 7 else n


def _parse_field(expr: str, min_v: int, max_v: int, *, sunday_seven: bool = False) -> tuple[frozenset[int], bool]:
    token = expr.strip()
    if not token or not all(c.isdigit() or c in "*/, -" for c in token):
        raise CronError("SCHED_002", "Biểu thức cron chứa ký tự không hợp lệ.")
    if token == "*":
        return frozenset(range(min_v, max_v + 1)), True

    values: set[int] = set()
    for part in token.split(","):
        part = part.strip()
        if not part:
            raise CronError("SCHED_002", "Biểu thức cron không hợp lệ.")
        step = 1
        if "/" in part:
            range_part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError("SCHED_002", "Bước lặp cron không hợp lệ.")
            step = int(step_s)
        else:
            range_part = part
        if range_part in ("", "*"):
            start, end = min_v, max_v
        elif "-" in range_part:
            a, b = range_part.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                raise CronError("SCHED_002", "Biểu thức cron không hợp lệ.")
            start, end = int(a), int(b)
        else:
            if not range_part.isdigit():
                raise CronError("SCHED_002", "Biểu thức cron không hợp lệ.")
            start = end = int(range_part)
        if sunday_seven:
            start, end = _norm_dow(start), _norm_dow(end)
        if start < min_v or end < min_v or start > max_v or end > max_v or start > end:
            raise CronError("SCHED_002", "Giá trị cron nằm ngoài phạm vi cho phép.")
        values.update(range(start, end + 1, step))
    return frozenset(values), False


def _parse(expression: str) -> tuple[frozenset[int], ...]:
    fields = expression.strip().split()
    if len(fields) != 5:
        raise CronError("SCHED_002", "Biểu thức cron phải có đúng 5 trường.")
    minute, hour, dom, month, dow = fields
    return (
        _parse_field(minute, 0, 59)[0],
        _parse_field(hour, 0, 23)[0],
        _parse_field(dom, 1, 31)[0],
        _parse_field(month, 1, 12)[0],
        _parse_field(dow, 0, 6, sunday_seven=True)[0],
        frozenset({1 if dom.strip() == "*" else 0}),
        frozenset({1 if dow.strip() == "*" else 0}),
    )


def _cron_dow(dt: datetime) -> int:
    """Python weekday Mon=0 → cron Sun=0."""
    return (dt.weekday() + 1) % 7


def _matches(dt: datetime, parsed: tuple[frozenset[int], ...]) -> bool:
    minutes, hours, doms, months, dows, dom_star, dow_star = parsed
    if dt.minute not in minutes or dt.hour not in hours or dt.month not in months:
        return False
    dom_ok = dt.day in doms
    dow_ok = _cron_dow(dt) in dows
    if 1 in dom_star and 1 in dow_star:
        return True
    if 1 in dom_star:
        return dow_ok
    if 1 in dow_star:
        return dom_ok
    return dom_ok or dow_ok


def iter_next_runs(expression: str, timezone: str, *, count: int, after: datetime | None = None) -> list[datetime]:
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise CronError("VAL_005", "Múi giờ không hợp lệ.") from exc
    parsed = _parse(expression)
    start = (after or datetime.now(tz)).astimezone(tz)
    # Next whole minute — current minute already "happened".
    cursor = (start.replace(second=0, microsecond=0) + timedelta(minutes=1))
    limit = cursor + _LOOKAHEAD
    found: list[datetime] = []
    while cursor <= limit and len(found) < count:
        if _matches(cursor, parsed):
            found.append(cursor)
        cursor += timedelta(minutes=1)
    return found


def describe_cron(expression: str) -> str:
    fields = expression.strip().split()
    if len(fields) != 5:
        return expression
    minute, hour, day_of_month, _month, day_of_week = fields
    time = f"{hour.zfill(2)}:{minute.zfill(2)}" if hour.isdigit() and minute.isdigit() else f"{hour}:{minute}"

    if hour.startswith("*/") and hour[2:].isdigit():
        return f"Mỗi {hour[2:]} giờ"
    if minute.startswith("*/") and minute[2:].isdigit() and hour == "*":
        return f"Mỗi {minute[2:]} phút"
    if hour == "*":
        return f"Mỗi giờ, phút thứ {minute}"
    if day_of_week != "*":
        days: list[str] = []
        for token in day_of_week.split(","):
            token = token.strip()
            if "-" in token:
                a, b = token.split("-", 1)
                try:
                    start, end = int(a), int(b)
                except ValueError:
                    continue
                if start == 7:
                    start = 0
                if end == 7:
                    end = 0
                if start <= end:
                    days.extend(_WEEKDAYS[i] for i in range(start, end + 1))
            else:
                try:
                    n = int(token)
                except ValueError:
                    continue
                if n == 7:
                    n = 0
                if 0 <= n <= 6:
                    days.append(_WEEKDAYS[n])
        label = ", ".join(days) if days else day_of_week
        return f"{label} lúc {time}"
    if day_of_month != "*":
        return f"Ngày {day_of_month} hàng tháng lúc {time}"
    return f"Mỗi ngày lúc {time}"


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def format_local(dt: datetime, timezone: str) -> str:
    tz = ZoneInfo(timezone)
    local = dt.astimezone(tz)
    return local.strftime("%H:%M %d/%m/%Y")


def validate_cron(expression: str, timezone: str) -> CronValidation:
    try:
        runs = iter_next_runs(expression, timezone, count=5)
    except CronError as exc:
        return CronValidation(valid=False, code=exc.code, message=exc.message)
    if len(runs) < 2:
        return CronValidation(
            valid=False,
            code="SCHED_002",
            message="Không tính được lần chạy tiếp theo từ biểu thức này.",
        )
    gap = (runs[1] - runs[0]).total_seconds()
    if gap < MIN_GAP_SECONDS:
        return CronValidation(
            valid=False,
            code="SCHED_003",
            message="Hai lần chạy phải cách nhau ít nhất 5 phút.",
        )
    return CronValidation(
        valid=True,
        cron_description=describe_cron(expression),
        next_5_runs_local=[format_local(dt, timezone) for dt in runs],
        next_run_at=to_iso(runs[0]),
    )


def next_run_iso(expression: str, timezone: str, *, after: datetime | None = None) -> str | None:
    try:
        runs = iter_next_runs(expression, timezone, count=1, after=after)
    except CronError:
        return None
    return to_iso(runs[0]) if runs else None
