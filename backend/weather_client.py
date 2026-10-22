"""Weather_Data_Service client: two-step fetch against api.weather.gov.

This module implements :class:`NwsWeatherClient`, the concrete
``WeatherClient`` (see the Protocol in ``forecast.py``) that fetches real
Weather_Data for a :class:`City` from the U.S. National Weather Service API
(api.weather.gov). The service is public and requires no API key, but it does
require a descriptive ``User-Agent`` header identifying the caller.

The fetch is a two-step call made entirely with the Python standard library
(:mod:`urllib.request`) over HTTPS, so no third-party HTTP dependency is added:

1. ``GET https://api.weather.gov/points/{lat},{lon}`` resolves the city's
   coordinates to a forecast gridpoint; the response's ``properties.forecast``
   field carries the URL for the gridpoint's forecast periods.
2. ``GET {properties.forecast}`` returns the forecast periods. The first
   (current) period drives the structured :class:`WeatherData`: its
   ``temperature``/``temperatureUnit`` become the temperature pair,
   ``shortForecast`` becomes ``conditions``, ``windSpeed`` + ``windDirection``
   become ``wind``, and ``detailedForecast`` becomes ``detailed_forecast``.

A single 10-second budget spans both calls (Requirement 2.3): the time already
spent on the first call is subtracted from the timeout allowed for the second.
The client raises :class:`WeatherUnavailable` on a timeout, an error status, an
unreachable service, or a malformed/empty response (Requirements 2.3, 2.4,
2.5). :class:`WeatherData` is built strictly from the response -- the client
never fabricates, defaults, or substitutes stale placeholder weather values
(Requirement 2.6). Content was rephrased for compliance with licensing
restrictions.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from models import City, WeatherData, WeatherUnavailable

# Total budget (seconds) shared across the two NWS calls (Requirement 2.3).
_TOTAL_BUDGET_SECONDS: float = 10.0

# Base name advertised in the User-Agent header; the contact is appended so the
# NWS can identify the caller, as its API requires.
_APP_NAME = "ca-weather-app"

# Fallback contact used only when no contact is configured via the constructor
# or the NWS_USER_AGENT_CONTACT environment variable.
_DEFAULT_CONTACT = "ca-weather-app@example.com"

# Environment variable the deployed Lambda sets to supply a real contact.
_CONTACT_ENV_VAR = "NWS_USER_AGENT_CONTACT"


class NwsWeatherClient:
    """Fetch real Weather_Data from api.weather.gov via a two-step call.

    Implements the ``WeatherClient`` Protocol consumed by
    ``forecast.generate_forecast``. The client is stateless beyond its
    configured ``User-Agent`` header, so a single instance is safe to reuse.

    The contact portion of the ``User-Agent`` is configurable so deployments
    can advertise a real address: pass ``contact`` explicitly, or leave it
    ``None`` to read the ``NWS_USER_AGENT_CONTACT`` environment variable,
    falling back to a sensible default when neither is set.
    """

    def __init__(
        self,
        contact: str | None = None,
        *,
        total_budget_seconds: float = _TOTAL_BUDGET_SECONDS,
    ) -> None:
        """Build a client with a descriptive ``User-Agent`` and time budget.

        Args:
            contact: contact string (e.g. an email or URL) embedded in the
                ``User-Agent`` header. When ``None``, the
                ``NWS_USER_AGENT_CONTACT`` environment variable is used, and if
                that is unset a default placeholder contact is used.
            total_budget_seconds: the total wall-clock budget shared across the
                two NWS calls. Defaults to 10 seconds (Requirement 2.3).
        """
        resolved_contact = (
            contact
            if contact is not None
            else os.environ.get(_CONTACT_ENV_VAR, _DEFAULT_CONTACT)
        )
        # Descriptive User-Agent: app name plus a contact, as the NWS API
        # requires. Sent on every request (both steps).
        self._user_agent = f"{_APP_NAME} ({resolved_contact})"
        self._total_budget_seconds = total_budget_seconds

    def fetch(self, city: City) -> WeatherData:
        """Return real Weather_Data for ``city`` or raise WeatherUnavailable.

        Performs the two-step NWS fetch within a single shared time budget and
        maps the first/current forecast period into a :class:`WeatherData`.

        Args:
            city: the supported :class:`City` whose coordinates are queried.

        Returns:
            A :class:`WeatherData` populated solely from the NWS response.

        Raises:
            WeatherUnavailable: on a timeout, an error status, an unreachable
                service, or a malformed/empty response.
        """
        # Single clock for the whole fetch so the 10s budget spans both calls.
        deadline = time.monotonic() + self._total_budget_seconds

        # Step 1: resolve coordinates to a forecast gridpoint URL.
        points_url = (
            f"https://api.weather.gov/points/{city.lat},{city.lon}"
        )
        points = self._get_json(points_url, deadline)
        forecast_url = self._extract_forecast_url(points)

        # Step 2: fetch the gridpoint forecast periods.
        forecast = self._get_json(forecast_url, deadline)
        period = self._extract_current_period(forecast)

        return self._build_weather_data(city, period)

    # -- HTTP -------------------------------------------------------------

    def _get_json(self, url: str, deadline: float) -> dict:
        """GET ``url`` within the remaining budget and parse the JSON body.

        Sends the descriptive ``User-Agent`` header. The per-request timeout is
        the time remaining until ``deadline`` so the two calls together never
        exceed the total budget.

        Raises:
            WeatherUnavailable: on an exhausted budget, a timeout, an error
                status, an unreachable service, or an unparseable body.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # The budget is already spent before this call could start.
            raise WeatherUnavailable(
                "Timed out fetching weather data from the Weather_Data_Service."
            )

        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": self._user_agent,
                "Accept": "application/geo+json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=remaining) as response:
                # Any non-2xx would already have raised HTTPError below; this
                # guards redirects or other unexpected success-ish statuses.
                status = getattr(response, "status", 200)
                if status is None or status < 200 or status >= 300:
                    raise WeatherUnavailable(
                        f"The Weather_Data_Service returned status {status}."
                    )
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # The service responded with an error status (4xx/5xx).
            raise WeatherUnavailable(
                f"The Weather_Data_Service returned an error status {exc.code}."
            ) from exc
        except socket.timeout as exc:
            # The read/connect exceeded the remaining budget.
            raise WeatherUnavailable(
                "Timed out fetching weather data from the Weather_Data_Service."
            ) from exc
        except urllib.error.URLError as exc:
            # Unreachable service, DNS failure, or a timeout surfaced as a
            # URLError with a socket.timeout reason.
            if isinstance(exc.reason, socket.timeout):
                raise WeatherUnavailable(
                    "Timed out fetching weather data from the "
                    "Weather_Data_Service."
                ) from exc
            raise WeatherUnavailable(
                "The Weather_Data_Service is unreachable."
            ) from exc

        if not raw:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned an empty response."
            )

        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            ) from exc

        if not isinstance(parsed, dict):
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        return parsed

    # -- Response parsing -------------------------------------------------

    @staticmethod
    def _extract_forecast_url(points: dict) -> str:
        """Read ``properties.forecast`` from the points response.

        Raises:
            WeatherUnavailable: when the field is missing or not a non-empty
                string (a malformed response).
        """
        properties = points.get("properties")
        if not isinstance(properties, dict):
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        forecast_url = properties.get("forecast")
        if not isinstance(forecast_url, str) or not forecast_url:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        return forecast_url

    @staticmethod
    def _extract_current_period(forecast: dict) -> dict:
        """Return the first/current period from the forecast response.

        Raises:
            WeatherUnavailable: when ``properties.periods`` is missing, empty,
                or the first period is not an object (malformed/empty).
        """
        properties = forecast.get("properties")
        if not isinstance(properties, dict):
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        periods = properties.get("periods")
        if not isinstance(periods, list) or not periods:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned an empty forecast."
            )
        period = periods[0]
        if not isinstance(period, dict):
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        return period

    @staticmethod
    def _build_weather_data(city: City, period: dict) -> WeatherData:
        """Map a forecast period into :class:`WeatherData`.

        Every field is taken from the period -- nothing is fabricated or
        defaulted (Requirement 2.6). A missing required field makes the
        response malformed.

        Raises:
            WeatherUnavailable: when a required period field is missing.
        """
        temperature = period.get("temperature")
        temperature_unit = period.get("temperatureUnit")
        short_forecast = period.get("shortForecast")
        wind_speed = period.get("windSpeed")
        wind_direction = period.get("windDirection")
        detailed_forecast = period.get("detailedForecast")

        # Each mapped field must be present; absence is a malformed response.
        # ``temperature`` may legitimately be a number from the NWS, so it is
        # checked for None rather than falsiness (0 is a valid temperature).
        if temperature is None or not isinstance(
            temperature_unit, str
        ) or not temperature_unit:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        if not isinstance(short_forecast, str) or not short_forecast:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        if not isinstance(wind_speed, str) or not wind_speed:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        if not isinstance(wind_direction, str) or not wind_direction:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )
        if not isinstance(detailed_forecast, str) or not detailed_forecast:
            raise WeatherUnavailable(
                "The Weather_Data_Service returned a malformed response."
            )

        # Combine NWS windSpeed + windDirection, e.g. "5 mph" + "W" -> "5 mph W".
        wind = f"{wind_speed} {wind_direction}"

        retrieved_at = datetime.now(timezone.utc).isoformat()

        return WeatherData(
            city=city,
            temperature=str(temperature),
            temperature_unit=temperature_unit,
            conditions=short_forecast,
            wind=wind,
            detailed_forecast=detailed_forecast,
            retrieved_at=retrieved_at,
        )
