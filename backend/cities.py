"""Supported California city catalog and pure lookup helpers.

This module is the Backend_Service's single source of truth for the supported
California cities (Requirements 5.1, 5.2). The catalog is a fixed, in-code
tuple of :class:`City` entries, each carrying the latitude/longitude
coordinates used to query the Weather_Data_Service (api.weather.gov) for real
Weather_Data (Requirement 2.1).

The lookups are pure functions with no I/O: :func:`supported_cities` returns
the full catalog, :func:`get_city` resolves a city id to a :class:`City` (or
``None`` for an unknown id), and :func:`is_supported_city` reports whether an
id is in the catalog.

City ids are kebab-case (e.g. ``"san-francisco"``). Coordinates are real
latitude/longitude values for each city's downtown/city-center so the
Weather_Data_Service can resolve them to a forecast gridpoint.
"""

from __future__ import annotations

from models import City

# ---------------------------------------------------------------------------
# Supported California city catalog (single source of truth)
# ---------------------------------------------------------------------------
#
# Coordinates are real decimal degrees (WGS84) for each city center. Latitude
# is positive (northern hemisphere); longitude is negative (western
# hemisphere), as required by the NWS ``/points/{lat},{lon}`` endpoint.

_CATALOG: tuple[City, ...] = (
    City(id="los-angeles", name="Los Angeles", lat=34.0522, lon=-118.2437),
    City(id="san-francisco", name="San Francisco", lat=37.7749, lon=-122.4194),
    City(id="san-diego", name="San Diego", lat=32.7157, lon=-117.1611),
    City(id="sacramento", name="Sacramento", lat=38.5816, lon=-121.4944),
    City(id="san-jose", name="San Jose", lat=37.3382, lon=-121.8863),
    City(id="fresno", name="Fresno", lat=36.7378, lon=-119.7871),
    City(id="oakland", name="Oakland", lat=37.8044, lon=-122.2712),
    City(id="long-beach", name="Long Beach", lat=33.7701, lon=-118.1937),
)

# Index the catalog by id for O(1) lookups. Built once at import time from the
# single-source-of-truth tuple above.
_BY_ID: dict[str, City] = {city.id: city for city in _CATALOG}


def supported_cities() -> list[City]:
    """Return the full supported California city catalog.

    Returns a new list (in catalog order) so callers cannot mutate the
    underlying source of truth.
    """
    return list(_CATALOG)


def get_city(city_id: str) -> City | None:
    """Return the :class:`City` for ``city_id``, or ``None`` if unknown.

    Args:
        city_id: a kebab-case city identifier, e.g. ``"san-francisco"``.

    Returns:
        The matching :class:`City`, or ``None`` when ``city_id`` is not in the
        supported catalog.
    """
    return _BY_ID.get(city_id)


def is_supported_city(city_id: str) -> bool:
    """Return ``True`` when ``city_id`` is in the supported catalog.

    Args:
        city_id: a kebab-case city identifier, e.g. ``"san-francisco"``.

    Returns:
        ``True`` if the id is a supported California city, else ``False``.
    """
    return city_id in _BY_ID
