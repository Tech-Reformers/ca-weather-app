"""Shared dataclasses and internal result types for the Backend_Service.

This module is the single source of truth for the core data models
(:class:`City`, :class:`WeatherData`, :class:`Forecast`) and the internal
result types used by the pure orchestration core (``forecast.py``) and the
pure validation helpers (``validation.py``):

* :class:`ValidationResult` -- the outcome of city-value validation
  (``INVALID`` or ``WELL_FORMED`` carrying a normalized ``city_id``).
* :class:`ContentResult` -- the outcome of summary content-bounds validation
  (``OK`` or ``UNAVAILABLE``).
* :class:`ForecastResult` -- the outcome of ``generate_forecast``: either a
  :class:`Success` carrying the Forecast parts, or an :class:`Error` carrying a
  typed error ``code`` and human-readable ``message``.
* :class:`WeatherUnavailable` -- the exception the weather_client raises when a
  real Weather_Data fetch fails.

The result types are modeled as small frozen dataclasses with convenience
predicates (``invalid``/``ok``) and constructors (``ValidationResult.invalid``,
``ValidationResult.well_formed``, ``ForecastResult.success``,
``ForecastResult.error``) so orchestration code can distinguish success from a
typed error cleanly without leaking I/O concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Error codes (ForecastResult.Error.code / HTTP mapping in handler.py)
# ---------------------------------------------------------------------------


class ErrorCode(str, Enum):
    """The typed error codes an ``Error`` ForecastResult can carry.

    These are the codes referenced throughout the design's error-handling
    table; ``handler.py`` maps each to an HTTP status. Each member is a ``str``
    subclass so the value serializes directly into the JSON error envelope.
    """

    INVALID_CITY = "INVALID_CITY"
    CITY_NOT_SUPPORTED = "CITY_NOT_SUPPORTED"
    WEATHER_UNAVAILABLE = "WEATHER_UNAVAILABLE"
    FORECAST_UNAVAILABLE = "FORECAST_UNAVAILABLE"
    FORECAST_TIMEOUT = "FORECAST_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Module-level aliases so callers can write ``ErrorCode.INVALID_CITY`` or the
# bare ``INVALID_CITY`` name, matching the design's orchestration pseudocode.
INVALID_CITY = ErrorCode.INVALID_CITY
CITY_NOT_SUPPORTED = ErrorCode.CITY_NOT_SUPPORTED
WEATHER_UNAVAILABLE = ErrorCode.WEATHER_UNAVAILABLE
FORECAST_UNAVAILABLE = ErrorCode.FORECAST_UNAVAILABLE
FORECAST_TIMEOUT = ErrorCode.FORECAST_TIMEOUT
INTERNAL_ERROR = ErrorCode.INTERNAL_ERROR


# ---------------------------------------------------------------------------
# Core data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class City:
    """A supported California city in the Backend_Service catalog.

    Attributes:
        id: kebab-case identifier, e.g. ``"san-francisco"``.
        name: display name, e.g. ``"San Francisco"``.
        lat: latitude, used to query the Weather_Data_Service.
        lon: longitude, used to query the Weather_Data_Service.
    """

    id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class WeatherData:
    """Structured real Weather_Data returned by the weather_client.

    Contains only values obtained from the Weather_Data_Service -- never
    fabricated, default, or stale placeholder values (Requirement 2.6).

    Attributes:
        city: the :class:`City` the data was fetched for.
        temperature: temperature value, e.g. ``"78"`` (paired with the unit).
        temperature_unit: temperature unit, e.g. ``"F"``.
        conditions: NWS ``shortForecast``, e.g. ``"Sunny"``.
        wind: NWS ``windSpeed`` + ``windDirection``, e.g. ``"5 mph W"``.
        detailed_forecast: NWS ``detailedForecast`` prose.
        retrieved_at: ISO-8601 timestamp of when the data was fetched.
    """

    city: City
    temperature: str
    temperature_unit: str
    conditions: str
    wind: str
    detailed_forecast: str
    retrieved_at: str


@dataclass(frozen=True)
class Forecast:
    """The validated Forecast content returned on the success path.

    Attributes:
        summary: 1..5000 characters of AI summary grounded in WeatherData.
        historical_perspective: <= 1000 characters, truncated if the AI
            returned more (Requirement 4.6).
    """

    summary: str
    historical_perspective: str


# ---------------------------------------------------------------------------
# Internal result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of ``validate_city_value``.

    Either ``INVALID`` (null/non-string/empty/whitespace-only) or
    ``WELL_FORMED`` carrying a normalized (trimmed, lowercased) ``city_id``.

    Use the :attr:`invalid` predicate to branch, and read :attr:`city_id` only
    when the result is well-formed.
    """

    is_valid: bool
    city_id: str | None = None

    @property
    def invalid(self) -> bool:
        """True when the city value was INVALID."""
        return not self.is_valid

    @classmethod
    def invalid_result(cls) -> ValidationResult:
        """Construct an INVALID result (no ``city_id``)."""
        return cls(is_valid=False, city_id=None)

    @classmethod
    def well_formed(cls, city_id: str) -> ValidationResult:
        """Construct a WELL_FORMED result carrying the normalized ``city_id``."""
        return cls(is_valid=True, city_id=city_id)


@dataclass(frozen=True)
class ContentResult:
    """Outcome of ``validate_forecast_content``: ``OK`` or ``UNAVAILABLE``.

    Use the :attr:`ok` predicate to branch.
    """

    ok: bool

    @classmethod
    def ok_result(cls) -> ContentResult:
        """Construct an OK result (summary length within 1..5000)."""
        return cls(ok=True)

    @classmethod
    def unavailable(cls) -> ContentResult:
        """Construct an UNAVAILABLE result (summary empty or > 5000 chars)."""
        return cls(ok=False)


@dataclass(frozen=True)
class ForecastResult:
    """Outcome of ``generate_forecast``: a Success or a typed Error.

    This is a tagged union over :attr:`is_success`:

    * On success, :attr:`city`, :attr:`summary`, and
      :attr:`historical_perspective` are populated and :attr:`error_code` /
      :attr:`error_message` are ``None``.
    * On error, :attr:`error_code` and :attr:`error_message` are populated and
      no partial Forecast is carried (the Forecast fields stay ``None``).

    Construct results with :meth:`success` and :meth:`error`; branch on
    :attr:`is_success`.
    """

    is_success: bool
    city: City | None = None
    summary: str | None = None
    historical_perspective: str | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None

    @property
    def is_error(self) -> bool:
        """True when this result is a typed Error."""
        return not self.is_success

    @classmethod
    def success(
        cls, city: City, summary: str, historical_perspective: str
    ) -> ForecastResult:
        """Construct a Success carrying the matching City and Forecast parts."""
        return cls(
            is_success=True,
            city=city,
            summary=summary,
            historical_perspective=historical_perspective,
        )

    @classmethod
    def error(cls, code: ErrorCode, message: str) -> ForecastResult:
        """Construct a typed Error with a code and human-readable message."""
        return cls(is_success=False, error_code=code, error_message=message)

    def forecast(self) -> Forecast:
        """Return the Forecast content for a Success result.

        Raises:
            ValueError: if called on an Error result (no partial Forecast).
        """
        if not self.is_success:
            raise ValueError("forecast() is only available on a Success result")
        assert self.summary is not None
        assert self.historical_perspective is not None
        return Forecast(
            summary=self.summary,
            historical_perspective=self.historical_perspective,
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WeatherUnavailable(Exception):
    """Raised by the weather_client when a real Weather_Data fetch fails.

    Signals a timeout, a service error/unavailability, an unreachable service,
    or a malformed/empty response. ``generate_forecast`` catches this and maps
    it to the ``WEATHER_UNAVAILABLE`` error code; the AI_Forecast_Generator is
    never invoked when it is raised.
    """
