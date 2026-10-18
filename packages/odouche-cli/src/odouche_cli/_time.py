"""A moment as how far it is from now, for a table."""

from datetime import UTC, datetime, timedelta


_UNITS = (("day", timedelta(days=1)), ("hour", timedelta(hours=1)), ("minute", timedelta(minutes=1)))
_MINUTE = timedelta(minutes=1)


# What the tests replace: the time.
def _now() -> datetime:
    return datetime.now(UTC)


def ago(moment: datetime) -> str:
    """Say how long ago a moment was. One a minute or more ahead, as a skewed clock gives, is said as ahead."""
    delta = _now() - moment
    return f"in {_span(-delta)}" if -delta >= _MINUTE else f"{_span(delta)} ago"


def left(moment: datetime) -> str:
    """Say how far ahead a moment is."""
    return f"in {_span(moment - _now())}"


def elapsed(since: datetime) -> str:
    """Say how long has passed since a moment, as `m:ss` or `h:mm:ss`. A moment ahead is `0:00`."""
    minutes, seconds = divmod(max(int((_now() - since).total_seconds()), 0), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def _span(delta: timedelta) -> str:
    """Say a duration in its largest whole unit."""
    for name, unit in _UNITS:
        count = delta // unit
        if count >= 1:
            return f"{count} {name}{'' if count == 1 else 's'}"
    return "less than a minute"
