"""Pure input- and content-validation functions for the Backend_Service.

This module holds the PBT-testable validation core. Every function here is a
pure function: it performs no I/O, has no side effects, and depends only on its
arguments. The functions are:

* :func:`validate_city_value` -- classify a raw request city value as INVALID
  (null/non-string/empty/whitespace-only) or WELL_FORMED, normalizing a
  well-formed value to a trimmed, lowercased ``city_id`` (Requirement 5.4).
* :func:`validate_forecast_content` -- classify AI summary content as OK when
  ``1 <= len <= 5000`` else UNAVAILABLE (Requirements 3.5, 3.6).
* :func:`has_historical_perspective` -- report whether an AI generator result
  carries a non-empty Historical_Perspective (Requirement 4.4).
* :func:`truncate_historical_perspective` -- cap a Historical_Perspective at
  1000 characters, returning it unchanged when already within bound
  (Requirement 4.6).
"""

from __future__ import annotations

from models import ContentResult, ValidationResult

# Bounds drawn from the acceptance criteria.
MIN_CONTENT_LENGTH = 1
MAX_CONTENT_LENGTH = 5000
MAX_HISTORICAL_PERSPECTIVE_LENGTH = 1000


def validate_city_value(raw: object) -> ValidationResult:
    """Validate and normalize a raw request city value.

    A value is INVALID when it is ``None``, not a ``str``, empty, or
    whitespace-only. Otherwise it is WELL_FORMED, carrying a normalized
    ``city_id`` that is the input trimmed of surrounding whitespace and
    lowercased.

    The normalization (``strip().lower()``) is idempotent:
    ``normalize(normalize(x)) == normalize(x)`` for any string (Requirement
    5.4, and Property 3 in the design).

    Args:
        raw: the raw city value from the request; may be any object.

    Returns:
        A :class:`ValidationResult`: ``INVALID`` for a null/non-string/empty/
        whitespace-only value, otherwise ``WELL_FORMED`` with the normalized
        ``city_id``.
    """
    if raw is None or not isinstance(raw, str):
        return ValidationResult.invalid_result()
    normalized = raw.strip().lower()
    if not normalized:
        return ValidationResult.invalid_result()
    return ValidationResult.well_formed(normalized)


def validate_forecast_content(content: str) -> ContentResult:
    """Classify AI summary content by length bounds.

    Content is OK exactly when its length is in ``[1, 5000]`` inclusive;
    otherwise (empty, or exceeding 5000 characters) it is UNAVAILABLE
    (Requirements 3.5, 3.6). Boundaries of interest: length 0 and 5001 are
    UNAVAILABLE; length 1 and 5000 are OK.

    Args:
        content: the AI-generated summary text.

    Returns:
        A :class:`ContentResult`: ``OK`` when within bounds, else
        ``UNAVAILABLE``.
    """
    length = len(content)
    if MIN_CONTENT_LENGTH <= length <= MAX_CONTENT_LENGTH:
        return ContentResult.ok_result()
    return ContentResult.unavailable()


def has_historical_perspective(result: object) -> bool:
    """Report whether the AI result carries a non-empty Historical_Perspective.

    The ``result`` is the AI generator result object; its
    ``historical_perspective`` attribute holds the Historical_Perspective text.
    The perspective counts as present only when it is a string that is not
    empty and not whitespace-only (Requirement 4.4). A missing attribute, a
    ``None`` value, or a non-string value counts as absent.

    Args:
        result: the AI generator result object.

    Returns:
        ``True`` only when a non-empty Historical_Perspective is present.
    """
    perspective = getattr(result, "historical_perspective", None)
    if not isinstance(perspective, str):
        return False
    return bool(perspective.strip())


def truncate_historical_perspective(text: str) -> str:
    """Truncate a Historical_Perspective to at most 1000 characters.

    Returns ``text`` unchanged when its length is ``<= 1000``; otherwise
    returns its first 1000 characters. The result length is always
    ``min(len(text), 1000)`` (Requirement 4.6). Boundaries of interest: length
    999 and 1000 are returned unchanged; length 1001 is cut to 1000.

    Args:
        text: the Historical_Perspective text.

    Returns:
        The text unchanged if within bound, else its first 1000 characters.
    """
    if len(text) <= MAX_HISTORICAL_PERSPECTIVE_LENGTH:
        return text
    return text[:MAX_HISTORICAL_PERSPECTIVE_LENGTH]
