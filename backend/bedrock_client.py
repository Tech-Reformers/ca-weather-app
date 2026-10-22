"""AI_Forecast_Generator client: Amazon Bedrock Converse wrapper.

This module wraps the boto3 ``bedrock-runtime`` ``converse`` operation behind
the ``ForecastGenerator`` interface that ``forecast.py`` orchestrates. Given a
:class:`City` and the real :class:`WeatherData` just fetched from the
Weather_Data_Service, it:

1. Builds a Converse request whose single ``user`` message embeds the city name
   and the serialized ``WeatherData``, with a ``system`` prompt that grounds the
   summary strictly in that data (the model never invents weather values) and
   requires a Historical_Perspective comparing the forecast to typical/seasonal
   norms (Req 3.1, 3.3, 4.1).
2. Invokes ``bedrock-runtime.converse`` with ``modelId`` from config and an
   ``inferenceConfig.maxTokens`` sized to keep the combined output within the
   5000-character summary bound (Req 3.2).
3. Reads ``response["output"]["message"]["content"][0]["text"]`` and splits it
   on the labeled sections the system prompt instructs the model to emit,
   returning a :class:`GeneratedForecast` with ``summary`` and
   ``historical_perspective`` string attributes.

The boto3 client is injected (defaulting to ``boto3.client("bedrock-runtime")``)
and the model id is taken from a constructor argument or the ``BEDROCK_MODEL_ID``
environment variable, so the wrapper is unit-testable without calling the live
Bedrock service. The actual content-bounds validation and Historical_Perspective
presence/truncation live in ``forecast.py``/``validation.py``; this wrapper only
produces the raw ``summary`` and ``historical_perspective`` parts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from models import City, WeatherData

# ---------------------------------------------------------------------------
# Prompt conventions
# ---------------------------------------------------------------------------

# Labeled-section markers the model is instructed to emit and that the wrapper
# splits the response text on. Kept uppercase and on their own line so the split
# is unambiguous and resilient to surrounding whitespace.
SUMMARY_LABEL = "SUMMARY:"
HISTORICAL_LABEL = "HISTORICAL PERSPECTIVE:"

# The system prompt grounds the model in the supplied Weather_Data and dictates
# the two labeled sections the wrapper parses. It is explicit that the model
# must not invent weather values (Req 3.3) and must include a
# Historical_Perspective (Req 4.1).
SYSTEM_PROMPT = (
    "You are a weather forecast writer for a California weather app. "
    "You will be given the name of a California city and structured weather "
    "data that was retrieved from an authoritative weather service. "
    "Ground your writing strictly in the supplied weather data: never invent, "
    "guess, or add weather values that are not present in that data. "
    "Produce exactly two labeled sections, each starting on its own line with "
    "the given label in capital letters:\n"
    f"{SUMMARY_LABEL} a readable prose summary of the forecast, based only on "
    "the supplied weather data.\n"
    f"{HISTORICAL_LABEL} a historical perspective that compares the forecasted "
    "conditions to the typical or seasonal norms for this city at this time of "
    "year.\n"
    "Keep the combined output concise and well under 5000 characters. "
    "Do not add any other sections, headings, or preamble."
)

# Token budget. The 5000-character summary bound plus a <=1000-character
# Historical_Perspective is ~6000 characters of output. At a conservative ~4
# characters per token that is ~1500 tokens; 1500 keeps the combined output
# comfortably within the 5000-character summary bound enforced downstream while
# leaving headroom for the labels.
DEFAULT_MAX_TOKENS = 1500

# Environment variable the model id falls back to when not passed explicitly.
MODEL_ID_ENV_VAR = "BEDROCK_MODEL_ID"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedForecast:
    """Raw AI output split into the two Forecast parts.

    Exposes the ``summary`` and ``historical_perspective`` string attributes
    that ``forecast.py`` reads (matching the ``ForecastGenerator`` Protocol).
    Content-bounds validation and Historical_Perspective truncation are applied
    downstream, not here.
    """

    summary: str
    historical_perspective: str


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class BedrockForecastGenerator:
    """AI_Forecast_Generator backed by the Bedrock Converse API.

    Implements the ``ForecastGenerator`` interface (``generate(city, weather)``
    returning an object with ``summary`` and ``historical_perspective``) by
    building a Converse request, invoking ``bedrock-runtime.converse``, and
    splitting the returned text on the labeled sections.
    """

    def __init__(
        self,
        model_id: str | None = None,
        client: Any | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        """Construct the generator.

        Args:
            model_id: the Bedrock model id to invoke. When ``None``, falls back
                to the ``BEDROCK_MODEL_ID`` environment variable.
            client: an optional injected boto3 ``bedrock-runtime`` client. When
                ``None``, a default ``boto3.client("bedrock-runtime")`` is
                created lazily (so importing this module never requires boto3 or
                AWS credentials).
            max_tokens: the ``inferenceConfig.maxTokens`` cap, sized to keep the
                combined output within the 5000-character summary bound.

        Raises:
            ValueError: if no model id is provided and ``BEDROCK_MODEL_ID`` is
                not set.
        """
        resolved_model_id = model_id or os.environ.get(MODEL_ID_ENV_VAR)
        if not resolved_model_id:
            raise ValueError(
                "A Bedrock model id is required: pass model_id or set the "
                f"{MODEL_ID_ENV_VAR} environment variable."
            )
        self._model_id = resolved_model_id
        self._max_tokens = max_tokens

        if client is None:
            # Import boto3 lazily so the module imports cleanly without boto3
            # installed or AWS credentials configured (e.g. during unit tests
            # that inject a fake client).
            import boto3

            client = boto3.client("bedrock-runtime")
        self._client = client

    def generate(self, city: City, weather: WeatherData) -> GeneratedForecast:
        """Summarize ``weather`` for ``city`` and add a Historical_Perspective.

        Builds and sends the Converse request, then splits the returned text
        into a ``summary`` and a ``historical_perspective``.

        Args:
            city: the supported :class:`City` the forecast is for.
            weather: the real :class:`WeatherData` fetched for that city.

        Returns:
            A :class:`GeneratedForecast` with the two parsed parts.
        """
        prompt = _build_prompt(city, weather)
        response = self._client.converse(
            modelId=self._model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ],
            system=[{"text": SYSTEM_PROMPT}],
            inferenceConfig={"maxTokens": self._max_tokens},
        )
        text = response["output"]["message"]["content"][0]["text"]
        summary, historical_perspective = _split_response(text)
        return GeneratedForecast(
            summary=summary,
            historical_perspective=historical_perspective,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_prompt(city: City, weather: WeatherData) -> str:
    """Build the user-message text embedding the city and serialized data.

    The serialized ``WeatherData`` is rendered as explicit labeled fields so the
    model sees every retrieved value and can ground its summary strictly in
    them (Req 3.1, 3.3).
    """
    return (
        f"City: {city.name}, California\n"
        "Weather data (use only these values; do not invent any others):\n"
        f"- Temperature: {weather.temperature} {weather.temperature_unit}\n"
        f"- Conditions: {weather.conditions}\n"
        f"- Wind: {weather.wind}\n"
        f"- Detailed forecast: {weather.detailed_forecast}\n"
        f"- Data retrieved at: {weather.retrieved_at}\n"
        "\n"
        f"Write the {SUMMARY_LABEL} and {HISTORICAL_LABEL} sections as "
        "instructed."
    )


def _split_response(text: str) -> tuple[str, str]:
    """Split the model output into (summary, historical_perspective).

    Locates the two labeled sections the system prompt instructs the model to
    emit. The text before the Historical_Perspective label (with the optional
    ``SUMMARY:`` label stripped) becomes the summary; the text after it becomes
    the Historical_Perspective. Both parts are whitespace-trimmed.

    Parsing is case-insensitive on the labels and tolerant of a missing
    ``SUMMARY:`` label. If the Historical_Perspective label is absent, the whole
    text becomes the summary and the Historical_Perspective is empty, which the
    downstream ``has_historical_perspective`` check turns into a
    ``FORECAST_UNAVAILABLE`` error (Req 4.4).
    """
    upper = text.upper()
    hp_index = upper.find(HISTORICAL_LABEL)

    if hp_index == -1:
        # No Historical_Perspective section emitted; return the whole (label-
        # stripped) text as the summary and an empty Historical_Perspective.
        return _strip_label(text, SUMMARY_LABEL).strip(), ""

    summary_part = text[:hp_index]
    historical_part = text[hp_index + len(HISTORICAL_LABEL) :]

    summary = _strip_label(summary_part, SUMMARY_LABEL).strip()
    historical_perspective = historical_part.strip()
    return summary, historical_perspective


def _strip_label(text: str, label: str) -> str:
    """Remove a leading ``label`` (case-insensitive) from ``text`` if present."""
    stripped = text.lstrip()
    if stripped.upper().startswith(label):
        return stripped[len(label) :]
    return text
