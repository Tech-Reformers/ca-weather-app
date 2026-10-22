"""AWS Lambda entry point and HTTP response contract for the Backend_Service.

This module is the Backend_Service's Lambda handler. It parses the API Gateway
proxy event, dispatches to one of the two routes, invokes the pure
orchestration core (``forecast.generate_forecast``) with the real external
clients, maps the outcome to an HTTP status, and shapes the Lambda proxy
integration output (``statusCode``, ``headers``, ``body``, ``isBase64Encoded``)
with CORS headers on every response (Requirements 1.2, 1.4, 7.2, 7.3, 7.5,
8.5).

Routing (API Gateway HTTP API v2 proxy integration):

* ``GET /cities`` -- return the supported California city catalog shaped as
  ``{"cities": [{"id", "name"}, ...]}`` (the design's GET /cities response).
* ``POST /forecast`` -- parse the JSON body, extract ``city``, and call
  ``generate_forecast`` with the real :class:`NwsWeatherClient` and a
  15-second-capped :class:`BedrockForecastGenerator`.

Internal timeouts:

* The weather fetch has a 10-second budget enforced inside
  :class:`NwsWeatherClient` itself (Requirement 2.3).
* The AI invocation has a 15-second cap enforced here by wrapping the generator
  in :class:`_TimeBoundedGenerator`, which runs ``generate`` on a worker thread
  and abandons it if it exceeds the cap. A timeout surfaces as a
  ``FORECAST_UNAVAILABLE`` error with no partial Forecast (Requirements 3.4,
  4.5), matching the design's error table (an AI timeout is folded into
  ``FORECAST_UNAVAILABLE``).

Error-code -> HTTP mapping (the design's error table):

======================  ====  =======================================
code                    HTTP  meaning
======================  ====  =======================================
INVALID_CITY            400   empty/null/malformed city value
CITY_NOT_SUPPORTED      404   well-formed but not in the catalog
WEATHER_UNAVAILABLE     503   weather fetch failed
FORECAST_UNAVAILABLE    502   AI empty/oversized/timed out/no HP
FORECAST_TIMEOUT        504   backend exceeded the API timeout
INTERNAL_ERROR          500   unexpected backend execution error
(success)               200   Forecast returned
======================  ====  =======================================

A top-level ``try``/``except`` guard converts any unexpected exception into an
``INTERNAL_ERROR`` (500) error envelope so the Backend_Service never leaks a
stack trace or partial/malformed Forecast data to the client (Requirements 7.5,
8.5).
"""

from __future__ import annotations

import concurrent.futures
import json
from typing import Any

from bedrock_client import BedrockForecastGenerator
from cities import supported_cities
from forecast import ForecastGenerator, generate_forecast
from models import (
    City,
    ErrorCode,
    FORECAST_UNAVAILABLE,
    ForecastResult,
    INTERNAL_ERROR,
    WeatherData,
)
from weather_client import NwsWeatherClient

# ---------------------------------------------------------------------------
# HTTP / CORS constants
# ---------------------------------------------------------------------------

# The AI invocation cap (seconds). The weather fetch's 10s budget is enforced
# inside NwsWeatherClient; this bounds the Bedrock Converse call (Req 3.4, 4.5).
_AI_INVOCATION_BUDGET_SECONDS: float = 15.0

# CORS headers set on every response (success and error) so the browser accepts
# the cross-origin response from the S3-hosted SPA (Requirement for the
# direct-origin baseline: Access-Control-Allow-Origin/Methods/Headers).
_CORS_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# Error-code -> HTTP status mapping (the design's error table).
_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_CITY: 400,
    ErrorCode.CITY_NOT_SUPPORTED: 404,
    ErrorCode.WEATHER_UNAVAILABLE: 503,
    ErrorCode.FORECAST_UNAVAILABLE: 502,
    ErrorCode.FORECAST_TIMEOUT: 504,
    ErrorCode.INTERNAL_ERROR: 500,
}


# ---------------------------------------------------------------------------
# AI-invocation time cap
# ---------------------------------------------------------------------------


class _TimeBoundedGenerator:
    """Wrap a ``ForecastGenerator`` to cap ``generate`` at a time budget.

    Runs the wrapped generator's ``generate`` on a single-use worker thread and
    waits at most ``budget_seconds`` for it. If the budget elapses first, a
    :class:`TimeoutError` is raised (which the orchestration maps to
    ``FORECAST_UNAVAILABLE``) and the abandoned worker thread is left to finish
    on its own -- its result is discarded, so no partial Forecast is returned
    (Requirements 3.4, 4.5).

    This is a pragmatic cap: Python cannot forcibly kill the worker thread, but
    because each Lambda invocation handles a single request and the wrapped
    boto3 client call will itself eventually return or error, abandoning the
    result is sufficient to enforce the user-visible 15-second bound.
    """

    def __init__(
        self, inner: ForecastGenerator, budget_seconds: float
    ) -> None:
        """Wrap ``inner`` with a ``budget_seconds`` cap on ``generate``."""
        self._inner = inner
        self._budget_seconds = budget_seconds

    def generate(self, city: City, weather: WeatherData) -> object:
        """Invoke the wrapped generator, raising TimeoutError past the budget.

        Args:
            city: the supported :class:`City` the forecast is for.
            weather: the real :class:`WeatherData` fetched for that city.

        Returns:
            The wrapped generator's result when it completes within the budget.

        Raises:
            TimeoutError: when the wrapped ``generate`` exceeds the budget.
        """
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._inner.generate, city, weather)
        try:
            return future.result(timeout=self._budget_seconds)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(
                "The AI_Forecast_Generator did not respond within the "
                "15-second budget."
            ) from exc
        finally:
            # Do not block shutdown on the (possibly still-running) worker; its
            # result is abandoned so no partial Forecast is ever surfaced.
            executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda proxy integration entry point.

    Parses the proxy ``event``, routes to ``GET /cities`` or
    ``POST /forecast``, and returns a Lambda proxy output object. Every return
    path -- success, mapped error, or the top-level guard -- produces the proxy
    output format with CORS headers (Requirements 1.2, 1.4, 7.5, 8.5).

    Args:
        event: the API Gateway proxy event (HTTP API v2 shape preferred).
        context: the Lambda context object (unused; present for the contract).

    Returns:
        A Lambda proxy output dict: ``statusCode``, ``headers``, ``body`` (a
        JSON string), and ``isBase64Encoded`` (always ``False``).
    """
    try:
        method, path = _parse_route(event)

        if method == "GET" and path == "/cities":
            return _handle_cities()
        if method == "POST" and path == "/forecast":
            return _handle_forecast(event)

        # Unknown route: a well-formed proxy response in the same envelope.
        return _error_response(
            ErrorCode.INVALID_CITY,
            "No route matches the requested method and path.",
            status=404,
        )
    except Exception:  # noqa: BLE001 -- top-level guard (Req 7.5, 8.5)
        # Any unexpected error becomes a clean INTERNAL_ERROR (500); never leak
        # a stack trace or partial/malformed Forecast data to the client.
        return _error_response(
            INTERNAL_ERROR,
            "An internal error occurred while processing the request.",
        )


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------


def _parse_route(event: Any) -> tuple[str, str]:
    """Extract the (method, path) from an API Gateway proxy event.

    Targets the HTTP API v2 proxy shape primarily (the CDK uses ``HttpApi``),
    reading ``requestContext.http.method`` / ``requestContext.http.path`` (or
    ``rawPath``), and falling back to the v2 ``routeKey`` (e.g. ``"GET
    /cities"``) and then to the REST/v1 ``httpMethod`` / ``path`` fields so a
    variety of event shapes route correctly.

    Args:
        event: the proxy event (expected to be a mapping).

    Returns:
        A ``(method, path)`` tuple. ``method`` is upper-cased; ``path`` has any
        trailing slash (other than the root) trimmed so ``/cities/`` matches
        ``/cities``. Missing pieces yield empty strings, which route to the
        unknown-route branch.
    """
    if not isinstance(event, dict):
        return "", ""

    method = ""
    path = ""

    request_context = event.get("requestContext")
    if isinstance(request_context, dict):
        http = request_context.get("http")
        if isinstance(http, dict):
            # HTTP API v2 primary shape.
            method = http.get("method") or ""
            path = http.get("path") or ""

    # v1 / REST fallback for the method.
    if not method:
        method = event.get("httpMethod") or ""

    # Path fallbacks: v2 rawPath, then v1 path.
    if not path:
        path = event.get("rawPath") or event.get("path") or ""

    # routeKey fallback (e.g. "GET /cities") covers events that omit the http
    # block but carry the v2 routeKey.
    if (not method or not path) and isinstance(event.get("routeKey"), str):
        route_key = event["routeKey"].strip()
        if route_key and route_key != "$default" and " " in route_key:
            key_method, key_path = route_key.split(" ", 1)
            method = method or key_method
            path = path or key_path

    method = method.upper()
    path = _normalize_path(path)
    return method, path


def _normalize_path(path: str) -> str:
    """Trim a single trailing slash (except for the root path ``/``)."""
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/") or "/"
    return path


def _extract_raw_city(event: Any) -> object:
    """Return the raw ``city`` value from a ``POST /forecast`` JSON body.

    Reads the proxy ``body`` (base64-decoding it when ``isBase64Encoded`` is
    set), parses it as JSON, and returns its ``city`` field. A missing body, a
    body that is not a JSON object, or a missing ``city`` yields ``None`` so the
    downstream ``validate_city_value`` classifies it as ``INVALID_CITY``
    (Requirement 5.4). A body that is not valid JSON raises, which the top-level
    guard would otherwise turn into INTERNAL_ERROR; to keep a bad body a client
    error, a parse failure is treated as a ``None`` city here.

    Args:
        event: the proxy event (expected to be a mapping with a ``body``).

    Returns:
        The raw ``city`` value (any JSON type) or ``None`` when absent/unparsable.
    """
    if not isinstance(event, dict):
        return None

    body = event.get("body")
    if body is None:
        return None

    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", errors="replace")

    if event.get("isBase64Encoded") and isinstance(body, str):
        import base64

        try:
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            return None

    if not isinstance(body, str):
        return None

    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        # A malformed JSON body is treated as an absent city -> INVALID_CITY,
        # a client error, rather than an INTERNAL_ERROR.
        return None

    if not isinstance(parsed, dict):
        return None
    return parsed.get("city")


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _handle_cities() -> dict[str, Any]:
    """Build the ``GET /cities`` success response from the catalog.

    Returns:
        A 200 proxy response whose body is
        ``{"cities": [{"id", "name"}, ...]}`` in catalog order (the design's
        GET /cities response). Coordinates are intentionally omitted from the
        public payload.
    """
    cities = [
        {"id": city.id, "name": city.name} for city in supported_cities()
    ]
    return _response(200, {"cities": cities})


def _handle_forecast(event: Any) -> dict[str, Any]:
    """Handle ``POST /forecast``: orchestrate and map the result to HTTP.

    Extracts the raw ``city`` from the JSON body, constructs the real external
    clients (:class:`NwsWeatherClient` for the 10s weather fetch and a
    15s-capped :class:`BedrockForecastGenerator`), runs
    ``generate_forecast``, and maps the :class:`ForecastResult` to the success
    envelope (200) or the mapped error envelope (no partial Forecast on error).

    Args:
        event: the proxy event carrying the request body.

    Returns:
        A proxy response: the success envelope on a Success result, else the
        mapped error envelope.
    """
    raw_city = _extract_raw_city(event)

    # Construct the real external clients. NwsWeatherClient enforces the 10s
    # weather-fetch budget internally; the generator is wrapped to cap the AI
    # invocation at 15s (Req 2.3, 3.4, 4.5).
    weather_client = NwsWeatherClient()
    generator: ForecastGenerator = _TimeBoundedGenerator(
        BedrockForecastGenerator(), _AI_INVOCATION_BUDGET_SECONDS
    )

    try:
        result = generate_forecast(raw_city, weather_client, generator)
    except TimeoutError:
        # The AI invocation exceeded its 15s cap; surface as FORECAST_UNAVAILABLE
        # with no partial Forecast (Req 3.4, 4.5).
        return _error_response(
            FORECAST_UNAVAILABLE,
            "The forecast could not be generated in time for the requested "
            "city.",
        )

    return _forecast_result_to_response(result)


def _forecast_result_to_response(result: ForecastResult) -> dict[str, Any]:
    """Map a :class:`ForecastResult` to a proxy response.

    On Success, builds the ``{"city", "forecast"}`` envelope (200). On Error,
    builds the ``{"error": {"code", "message"}}`` envelope with the HTTP status
    from the mapping, carrying no partial Forecast data.
    """
    if result.is_success:
        assert result.city is not None
        assert result.summary is not None
        assert result.historical_perspective is not None
        payload = {
            "city": {"id": result.city.id, "name": result.city.name},
            "forecast": {
                "summary": result.summary,
                "historical_perspective": result.historical_perspective,
            },
        }
        return _response(200, payload)

    assert result.error_code is not None
    return _error_response(result.error_code, result.error_message or "")


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a Lambda proxy output object with CORS headers.

    Args:
        status: the HTTP status code.
        payload: the response body object, serialized to a JSON string.

    Returns:
        The proxy output dict (``statusCode``, ``headers``, ``body``,
        ``isBase64Encoded=False``).
    """
    return {
        "statusCode": status,
        "headers": dict(_CORS_HEADERS),
        "body": json.dumps(payload),
        "isBase64Encoded": False,
    }


def _error_response(
    code: ErrorCode, message: str, *, status: int | None = None
) -> dict[str, Any]:
    """Build an error-envelope proxy response for ``code``.

    Args:
        code: the typed error code; its HTTP status comes from the mapping
            unless ``status`` overrides it.
        message: a human-readable error message (Requirement 8.5).
        status: an optional explicit HTTP status (used for the unknown-route
            case); otherwise the status is taken from the error-code mapping.

    Returns:
        A proxy response whose body is ``{"error": {"code", "message"}}`` and
        which carries no partial Forecast data.
    """
    http_status = status if status is not None else _STATUS_BY_CODE.get(
        code, 500
    )
    payload = {
        "error": {
            "code": code.value,
            "message": message,
        }
    }
    return _response(http_status, payload)
