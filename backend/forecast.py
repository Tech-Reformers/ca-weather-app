"""Forecast orchestration: validate -> fetch Weather_Data -> summarize.

This module holds the pure orchestration core of the Backend_Service. The
single public function, :func:`generate_forecast`, wires together the pure
validation helpers (``validation.py``), the supported-city catalog
(``cities.py``), and two *injected* external dependencies -- a
``weather_client`` (the Weather_Data_Service wrapper) and a ``generator`` (the
AI_Forecast_Generator wrapper). Injecting both dependencies keeps this function
free of direct I/O so it can be unit- and property-tested with fakes/spies,
never touching api.weather.gov or Amazon Bedrock.

The orchestration order is fixed and enforces the acceptance criteria:

1. ``validate_city_value`` -- a null/non-string/empty/whitespace-only value
   yields ``INVALID_CITY`` and nothing downstream is called (Req 5.4).
2. ``is_supported_city`` -- a well-formed but unsupported id yields
   ``CITY_NOT_SUPPORTED`` with neither the weather_client nor the generator
   invoked (Req 1.4, 5.3).
3. ``get_city`` -- resolve the normalized id to the catalog :class:`City`.
4. ``weather_client.fetch(city)`` -- fetch real Weather_Data for the city's
   coordinates (Req 2.1, 2.2). A :class:`WeatherUnavailable` failure yields
   ``WEATHER_UNAVAILABLE`` and the generator is *not* invoked (Req 2.3, 2.4,
   2.5).
5. ``generator.generate(city, weather)`` -- the generator receives exactly the
   :class:`WeatherData` the weather_client returned (Req 3.1, 3.2, 3.3).
6. ``validate_forecast_content`` -- a summary outside ``[1, 5000]`` yields
   ``FORECAST_UNAVAILABLE`` (Req 3.5).
7. ``has_historical_perspective`` -- a missing/empty Historical_Perspective
   yields ``FORECAST_UNAVAILABLE`` with no partial Forecast (Req 4.4).
8. ``truncate_historical_perspective`` -- cap the Historical_Perspective at
   1000 characters (Req 4.6) and return a Success (Req 1.3, 4.1, 4.2).
"""

from __future__ import annotations

from typing import Protocol

from cities import get_city, is_supported_city
from models import (
    CITY_NOT_SUPPORTED,
    FORECAST_UNAVAILABLE,
    INVALID_CITY,
    WEATHER_UNAVAILABLE,
    City,
    ForecastResult,
    WeatherData,
    WeatherUnavailable,
)
from validation import (
    has_historical_perspective,
    truncate_historical_perspective,
    validate_city_value,
    validate_forecast_content,
)


class WeatherClient(Protocol):
    """The injected Weather_Data_Service dependency.

    Implementations fetch real Weather_Data for a :class:`City` and raise
    :class:`WeatherUnavailable` on timeout, service error/unavailability, an
    unreachable service, or a malformed/empty response.
    """

    def fetch(self, city: City) -> WeatherData:
        """Return real Weather_Data for ``city`` or raise WeatherUnavailable."""
        ...


class ForecastGenerator(Protocol):
    """The injected AI_Forecast_Generator dependency.

    Implementations summarize the supplied :class:`WeatherData` into readable
    prose and add a Historical_Perspective, returning a result object exposing
    ``summary`` and ``historical_perspective`` string attributes.
    """

    def generate(self, city: City, weather: WeatherData) -> object:
        """Return an AI result with ``summary`` and ``historical_perspective``."""
        ...


def generate_forecast(
    raw_city: object,
    weather_client: WeatherClient,
    generator: ForecastGenerator,
) -> ForecastResult:
    """Orchestrate a Forecast for ``raw_city`` using injected dependencies.

    Runs the fixed validate -> fetch -> summarize -> validate pipeline. Both
    the ``weather_client`` and the ``generator`` are injected so this function
    performs no direct I/O: the weather fetch and the AI invocation are reached
    only after the city has been validated and found supported, and the
    ``generator`` receives exactly the :class:`WeatherData` the
    ``weather_client`` returned.

    Args:
        raw_city: the raw city value from the request (any object).
        weather_client: the injected Weather_Data_Service wrapper.
        generator: the injected AI_Forecast_Generator wrapper.

    Returns:
        A :class:`ForecastResult`: a Success carrying the matching
        :class:`City`, the validated summary, and the (truncated)
        Historical_Perspective; or a typed Error (``INVALID_CITY``,
        ``CITY_NOT_SUPPORTED``, ``WEATHER_UNAVAILABLE``, or
        ``FORECAST_UNAVAILABLE``) with no partial Forecast.
    """
    # 1. Validate the raw city value. An INVALID value stops here -- neither the
    #    weather_client nor the generator is invoked (Req 5.4).
    validation = validate_city_value(raw_city)
    if validation.invalid:
        return ForecastResult.error(
            INVALID_CITY,
            "The city value is empty, null, or malformed.",
        )

    # 2. Enforce the supported-city scope. A well-formed but unsupported id
    #    stops here with no downstream call (Req 1.4, 5.3).
    city_id = validation.city_id
    assert city_id is not None  # guaranteed well-formed past the invalid check
    if not is_supported_city(city_id):
        return ForecastResult.error(
            CITY_NOT_SUPPORTED,
            "The requested city is not a supported California city.",
        )

    # 3. Resolve the normalized id to the catalog City (known to exist here).
    city = get_city(city_id)
    assert city is not None  # is_supported_city guaranteed a catalog entry

    # 4. Fetch real Weather_Data. A fetch failure maps to WEATHER_UNAVAILABLE
    #    and the generator is NOT invoked (Req 2.3, 2.4, 2.5).
    try:
        weather = weather_client.fetch(city)
    except WeatherUnavailable:
        return ForecastResult.error(
            WEATHER_UNAVAILABLE,
            "Weather data for the requested city is currently unavailable.",
        )

    # 5. Summarize. The generator receives exactly the WeatherData returned by
    #    the weather_client (Req 2.2, 3.1, 3.2, 3.3).
    result = generator.generate(city, weather)

    # 6. Validate summary bounds: empty or > 5000 chars is FORECAST_UNAVAILABLE
    #    (Req 3.5).
    if not validate_forecast_content(result.summary).ok:
        return ForecastResult.error(
            FORECAST_UNAVAILABLE,
            "The forecast could not be generated for the requested city.",
        )

    # 7. Require a non-empty Historical_Perspective; a missing one is
    #    FORECAST_UNAVAILABLE with no partial Forecast (Req 4.4).
    if not has_historical_perspective(result):
        return ForecastResult.error(
            FORECAST_UNAVAILABLE,
            "The forecast could not be generated for the requested city.",
        )

    # 8. Truncate the Historical_Perspective to <= 1000 chars and succeed
    #    (Req 4.6, 1.3, 4.1, 4.2).
    hp = truncate_historical_perspective(result.historical_perspective)
    return ForecastResult.success(city, result.summary, hp)
