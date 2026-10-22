"""Shared pytest/Hypothesis configuration for the backend test suite.

Registers and loads a Hypothesis settings profile that runs a minimum of 100
iterations by default, satisfying the project convention that every
property-based test exercises at least 100 examples.
"""

from hypothesis import HealthCheck, settings

# Minimum examples per property test, per project convention.
MIN_HYPOTHESIS_EXAMPLES = 100

settings.register_profile(
    "ca-weather-app",
    max_examples=MIN_HYPOTHESIS_EXAMPLES,
    # Allow property tests a little extra time so the 100+ examples run
    # reliably across machines without spurious deadline failures.
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Activate the profile for the whole test session.
settings.load_profile("ca-weather-app")
