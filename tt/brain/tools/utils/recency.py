from datetime import datetime, timedelta, timezone


def describe_recency(date_str: str, now: datetime) -> str:
    """Convert an ISO-ish timestamp into a friendly recency bucket."""
    normalized = (
        date_str.replace("+00", "+00:00") if date_str.endswith("+00") else date_str
    )
    try:
        memory_time = datetime.fromisoformat(normalized)
        if memory_time.tzinfo is None:
            memory_time = memory_time.replace(tzinfo=timezone.utc)
    except ValueError:
        memory_time = datetime.fromisoformat(date_str.split("+")[0]).replace(
            tzinfo=timezone.utc
        )

    if memory_time.date() == now.date():
        return "today"
    if memory_time.date() == (now - timedelta(days=1)).date():
        return "yesterday"

    start_of_week = datetime.combine(
        now.date() - timedelta(days=now.weekday()),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    start_of_last_week = start_of_week - timedelta(weeks=1)
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    start_of_last_month = (start_of_month - timedelta(days=1)).replace(day=1)

    if memory_time >= start_of_week:
        return "this week"
    if memory_time >= start_of_last_week:
        return "last week"
    if memory_time >= start_of_last_month:
        return "last month"
    return memory_time.date().isoformat()
