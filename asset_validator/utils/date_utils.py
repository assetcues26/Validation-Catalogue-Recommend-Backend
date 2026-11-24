"""Date parsing and acquisition-date validation."""

from datetime import date, datetime


def parse_acquisition_date(value) -> date | None:
    """Parse acquisition date from DD-MM-YYYY, DD/MM/YYYY, or ISO YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        raise ValueError(f"Invalid acquisition date type: {type(value).__name__}")

    s = value.strip()
    if not s:
        return None

    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    # ISO fallback: YYYY-MM-DD
    try:
        return date.fromisoformat(s[:10])
    except ValueError as exc:
        raise ValueError(
            f"acquisitiondate must be DD-MM-YYYY (e.g. 18-01-2021), got: {value!r}"
        ) from exc


def format_acquisition_date(value: date | str | None) -> str | None:
    """Format date as ISO YYYY-MM-DD for API output."""
    if value is None:
        return None
    if isinstance(value, str):
        parsed = parse_acquisition_date(value)
        if parsed is None:
            return value
        value = parsed
    return value.strftime("%Y-%m-%d")


def acquisition_invalid_reason(
    acquisition: date,
    release_year: int,
    *,
    model_end_year: int | None = None,
    today: date | None = None,
) -> str | None:
    """
    Return failure reason when acquisition date is invalid, else None.

    Reasons: invalid_release_year, before_release, future_date, after_discontinuation
    """
    if release_year < 1900 or release_year > 2100:
        return "invalid_release_year"

    reference_today = today or date.today()
    release_floor = date(release_year, 1, 1)

    if acquisition < release_floor:
        return "before_release"
    if acquisition > reference_today:
        return "future_date"
    if model_end_year is not None and acquisition.year > model_end_year:
        return "after_discontinuation"
    return None


def is_acquisition_valid(
    acquisition: date,
    release_year: int,
    *,
    model_end_year: int | None = None,
    today: date | None = None,
) -> bool:
    """PASS when acquisition is within model sales window and not in the future."""
    return acquisition_invalid_reason(
        acquisition,
        release_year,
        model_end_year=model_end_year,
        today=today,
    ) is None


def format_acquisition_invalid_message(
    reason: str,
    *,
    acquisition: date,
    release_year: int | None = None,
    model_end_year: int | None = None,
    today: date | None = None,
) -> str:
    """Human-readable message for acquisition_date_valid failure."""
    acq_str = format_acquisition_date(acquisition) or str(acquisition)
    reference_today = today or date.today()
    today_str = format_acquisition_date(reference_today) or str(reference_today)

    if reason == "future_date":
        return (
            f"acquisition_date_valid failed: Acquisition date {acq_str} is after "
            f"today ({today_str})."
        )
    if reason == "before_release":
        return (
            f"acquisition_date_valid failed: Acquisition date {acq_str} predates "
            f"model release year {release_year}."
        )
    if reason == "after_discontinuation":
        return (
            f"acquisition_date_valid failed: Acquisition date {acq_str} is after "
            f"model was discontinued (end year {model_end_year})."
        )
    if reason == "invalid_release_year":
        return "acquisition_date_valid failed: Model release year is missing or invalid."
    return f"acquisition_date_valid failed: Acquisition date {acq_str} is invalid."
