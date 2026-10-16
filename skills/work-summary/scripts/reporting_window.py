"""Exact reporting intervals without changing legacy rolling capture semantics."""

from datetime import datetime, timedelta
import re

from evidence import UTC, integer, iso, require, timestamp


MICROSECOND = timedelta(microseconds=1)
DAY_MICROSECONDS = 86_400_000_000
RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[Zz]|[+-]\d{2}:\d{2})\Z")


def endpoint(value):
    require(isinstance(value, str) and RFC3339.fullmatch(value) is not None,
            "Reporting endpoints need RFC3339 timestamps with a timezone and at most six fractional digits")
    return timestamp(value[:-1] + 'Z' if value.endswith('z') else value)


def elapsed_days(start, until):
    duration = until - start
    microseconds = ((duration.days * 86400 + duration.seconds) * 1_000_000
                    + duration.microseconds)
    require(microseconds > 0, "Reporting end must be after its start")
    return (microseconds + DAY_MICROSECONDS - 1) // DAY_MICROSECONDS


def resolve_window(days_text, now=None, *, since=None, until=None):
    """Return inclusive provider bounds and capture metadata for either mode."""
    explicit = since is not None or until is not None
    if explicit:
        require(days_text is None, "Choose rolling days or --since/--until, not both")
        require(since is not None and until is not None, "Both --since and --until are required")
        start, exclusive = endpoint(since), endpoint(until)
        days = elapsed_days(start, exclusive)
        end = exclusive - MICROSECOND
        extra = {'window_kind': 'explicit', 'until_exclusive': iso(exclusive)}
    else:
        days_text = '7' if days_text is None else days_text
        require(isinstance(days_text, str) and re.fullmatch(r'[0-9]+', days_text) is not None
                and int(days_text) > 0, "Days must be a positive integer")
        days = int(days_text)
        end = (now or datetime.now(UTC)).replace(microsecond=0)
        try:
            start = end - timedelta(days=days)
        except OverflowError as error:
            raise ValueError("Days exceed the supported date range") from error
        extra = {}
    return start, end, {'days': days, 'since_date': start.date().isoformat(),
                        'to_date': end.date().isoformat(), 'since_at': iso(start),
                        'to_at': iso(end), **extra}


def validate_window(metadata, start, end):
    kind = metadata.get('window_kind')
    require(kind is None or kind == 'explicit', "Unknown reporting window kind")
    if kind == 'explicit':
        require(start is not None and end is not None, "Explicit reporting window needs both timestamps")
        exclusive = endpoint(metadata.get('until_exclusive'))
        require(metadata['until_exclusive'] == iso(exclusive)
                and metadata['since_at'] == iso(endpoint(metadata['since_at']))
                and metadata['to_at'] == iso(endpoint(metadata['to_at'])),
                "Explicit reporting timestamps must use canonical UTC")
        require(end == exclusive - MICROSECOND and start <= end,
                "Invalid exclusive reporting boundary")
        require(integer(metadata.get('days'), 1) and metadata['days'] == elapsed_days(start, exclusive),
                "Invalid explicit reporting duration")
    else:
        require('window_kind' not in metadata and 'until_exclusive' not in metadata,
                "Exclusive boundary requires an explicit reporting window")
        if start or end:
            require(start is not None and end is not None
                    and end - start == timedelta(days=metadata['days']), "Invalid rolling date window")
    if start or end:
        require(start is not None and end is not None
                and start.date().isoformat() == metadata['since_date']
                and end.date().isoformat() == metadata['to_date'], "Date labels do not match timestamps")


def search_bounds(start, end):
    """Native search timestamps have second precision; exact filtering follows."""
    lower, upper = start.replace(microsecond=0), end.replace(microsecond=0)
    if end.microsecond:
        upper += timedelta(seconds=1)
    return lower, upper
