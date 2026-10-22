/*
 * CA Weather App - Frontend_SPA request flow.
 *
 * Responsibilities (Task 11.1):
 *   - On load: GET {API_BASE_URL}/cities and populate the selection control.
 *     Restrict selection strictly to ids present in the fetched catalog.
 *   - On submit: POST {API_BASE_URL}/forecast { city }. Show a loading
 *     indicator and disable further submissions while in flight. Apply a
 *     client-side timeout within the 30s budget.
 *   - On success: render the forecast summary and the Historical_Perspective
 *     as a distinct, labeled section; hide the loading indicator; clear any
 *     prior error.
 *   - On error / client timeout: hide the loading indicator; show an error
 *     message; retain the current selection AND any previously displayed
 *     forecast (do not clear the last good forecast).
 *
 * Framework-free vanilla JS, no build step. The API base URL is read from a
 * runtime config.json so the CDK BucketDeployment can generate it at deploy
 * time; a sensible fallback keeps the app functional if config.json is absent.
 */
(function () {
  "use strict";

  // Client-side timeout within the 30s budget (Req 1.6, 8.2). Kept just under
  // 30s so the browser aborts before the overall budget elapses.
  var CLIENT_TIMEOUT_MS = 29000;

  // Fallback base URL used when config.json is missing or has no apiBaseUrl.
  // Empty string means "same origin as the SPA" relative requests.
  var FALLBACK_API_BASE_URL = "";

  // --- DOM references -----------------------------------------------------
  var citySelect;
  var submitButton;
  var forecastForm;
  var loadingIndicator;
  var errorRegion;
  var resultsRegion;
  var forecastCityHeading;
  var forecastSummaryEl;
  var historicalPerspectiveSection;
  var historicalPerspectiveEl;

  // --- State --------------------------------------------------------------
  // The set of valid city ids fetched from the catalog. Submission is strictly
  // restricted to ids in this set (Req 5.1, 5.2).
  var catalogIds = Object.create(null);
  var requestInFlight = false;

  // ------------------------------------------------------------------------
  // Config loading
  // ------------------------------------------------------------------------

  /**
   * Resolve the API base URL from a runtime config.json, falling back to a
   * sensible default. Never throws; returns the fallback on any failure.
   * @returns {Promise<string>}
   */
  function loadApiBaseUrl() {
    return fetch("config.json", { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) {
          return null;
        }
        return res.json();
      })
      .then(function (cfg) {
        if (cfg && typeof cfg.apiBaseUrl === "string" && cfg.apiBaseUrl) {
          return stripTrailingSlash(cfg.apiBaseUrl);
        }
        return FALLBACK_API_BASE_URL;
      })
      .catch(function () {
        return FALLBACK_API_BASE_URL;
      });
  }

  function stripTrailingSlash(url) {
    return url.replace(/\/+$/, "");
  }

  // ------------------------------------------------------------------------
  // City catalog loading (on page load)
  // ------------------------------------------------------------------------

  /**
   * Fetch the supported-city catalog and populate the select. On failure,
   * surfaces an error and leaves the control disabled.
   * @param {string} apiBaseUrl
   * @returns {Promise<void>}
   */
  function loadCities(apiBaseUrl) {
    return fetch(apiBaseUrl + "/cities", {
      method: "GET",
      headers: { Accept: "application/json" }
    })
      .then(function (res) {
        if (!res.ok) {
          throw new Error("Failed to load cities (status " + res.status + ")");
        }
        return res.json();
      })
      .then(function (data) {
        var cities = data && Array.isArray(data.cities) ? data.cities : [];
        populateCitySelect(cities);
      })
      .catch(function () {
        showError(
          "Could not load the list of California cities. Please refresh the page to try again."
        );
        // Leave the select disabled with a clear placeholder.
        citySelect.innerHTML = "";
        var opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Cities unavailable";
        citySelect.appendChild(opt);
        citySelect.disabled = true;
        submitButton.disabled = true;
      });
  }

  /**
   * Populate the select from the fetched catalog and record the set of valid
   * ids. Only ids present here are ever submittable.
   * @param {Array<{id: string, name: string}>} cities
   */
  function populateCitySelect(cities) {
    catalogIds = Object.create(null);
    citySelect.innerHTML = "";

    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Choose a city…";
    citySelect.appendChild(placeholder);

    cities.forEach(function (city) {
      if (!city || typeof city.id !== "string") {
        return;
      }
      catalogIds[city.id] = true;
      var opt = document.createElement("option");
      opt.value = city.id;
      opt.textContent = typeof city.name === "string" ? city.name : city.id;
      citySelect.appendChild(opt);
    });

    citySelect.disabled = false;
    submitButton.disabled = false;
  }

  /**
   * Validate the current selection strictly against the fetched catalog.
   * @param {string} cityId
   * @returns {boolean}
   */
  function isValidSelection(cityId) {
    return typeof cityId === "string" && cityId !== "" && catalogIds[cityId] === true;
  }

  // ------------------------------------------------------------------------
  // Forecast request flow (on submit)
  // ------------------------------------------------------------------------

  /**
   * Handle a forecast submission.
   * @param {string} apiBaseUrl
   * @param {Event} event
   */
  function handleSubmit(apiBaseUrl, event) {
    event.preventDefault();

    // Block concurrent submissions while a request is in flight (Req 8.3).
    if (requestInFlight) {
      return;
    }

    var cityId = citySelect.value;

    // Strictly restrict submission to catalog ids (Req 5.2).
    if (!isValidSelection(cityId)) {
      showError("Please choose a supported California city before requesting a forecast.");
      return;
    }

    beginRequest();

    // Client-side timeout within the 30s budget (Req 1.6, 8.2).
    var controller = new AbortController();
    var timedOut = false;
    var timeoutId = setTimeout(function () {
      timedOut = true;
      controller.abort();
    }, CLIENT_TIMEOUT_MS);

    fetch(apiBaseUrl + "/forecast", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json"
      },
      body: JSON.stringify({ city: cityId }),
      signal: controller.signal
    })
      .then(function (res) {
        return res
          .json()
          .catch(function () {
            return null;
          })
          .then(function (body) {
            return { ok: res.ok, status: res.status, body: body };
          });
      })
      .then(function (result) {
        clearTimeout(timeoutId);
        if (result.ok) {
          renderForecast(result.body);
        } else {
          showError(extractErrorMessage(result.body));
        }
      })
      .catch(function () {
        clearTimeout(timeoutId);
        if (timedOut) {
          showError("The request timed out. Please try again.");
        } else {
          showError("The forecast could not be retrieved. Please try again.");
        }
      })
      .then(function () {
        // Always runs: tear down the in-flight UI state (Req 8.2, 8.4).
        endRequest();
      });
  }

  /**
   * Extract a human-readable message from an error envelope, with a fallback.
   * @param {any} body
   * @returns {string}
   */
  function extractErrorMessage(body) {
    if (
      body &&
      body.error &&
      typeof body.error.message === "string" &&
      body.error.message
    ) {
      return body.error.message;
    }
    return "The forecast could not be retrieved. Please try again.";
  }

  // ------------------------------------------------------------------------
  // UI state transitions
  // ------------------------------------------------------------------------

  function beginRequest() {
    requestInFlight = true;
    submitButton.disabled = true;
    citySelect.disabled = true;
    loadingIndicator.hidden = false;
  }

  function endRequest() {
    requestInFlight = false;
    submitButton.disabled = false;
    citySelect.disabled = false;
    loadingIndicator.hidden = true;
  }

  /**
   * Render a successful forecast. Clears any prior error (Req 8.4) and renders
   * the summary plus the Historical_Perspective in its own labeled section
   * (Req 1.5, 4.3).
   * @param {any} data
   */
  function renderForecast(data) {
    clearError();

    var cityName = "";
    if (data && data.city && typeof data.city.name === "string") {
      cityName = data.city.name;
    }

    var summary = "";
    var historical = "";
    if (data && data.forecast) {
      if (typeof data.forecast.summary === "string") {
        summary = data.forecast.summary;
      }
      if (typeof data.forecast.historical_perspective === "string") {
        historical = data.forecast.historical_perspective;
      }
    }

    forecastCityHeading.textContent = cityName
      ? "Forecast for " + cityName
      : "Forecast";
    forecastSummaryEl.textContent = summary;

    if (historical) {
      historicalPerspectiveEl.textContent = historical;
      historicalPerspectiveSection.hidden = false;
    } else {
      historicalPerspectiveEl.textContent = "";
      historicalPerspectiveSection.hidden = true;
    }

    resultsRegion.hidden = false;
  }

  /**
   * Show an error message. Deliberately does NOT clear the results region, so
   * any previously displayed forecast and the current selection are retained
   * (Req 1.6, 8.1).
   * @param {string} message
   */
  function showError(message) {
    errorRegion.textContent = message;
    errorRegion.hidden = false;
  }

  function clearError() {
    errorRegion.textContent = "";
    errorRegion.hidden = true;
  }

  // ------------------------------------------------------------------------
  // Initialization
  // ------------------------------------------------------------------------

  function cacheDomReferences() {
    citySelect = document.getElementById("city-select");
    submitButton = document.getElementById("submit-button");
    forecastForm = document.getElementById("forecast-form");
    loadingIndicator = document.getElementById("loading-indicator");
    errorRegion = document.getElementById("error-region");
    resultsRegion = document.getElementById("results-region");
    forecastCityHeading = document.getElementById("forecast-city-heading");
    forecastSummaryEl = document.getElementById("forecast-summary");
    historicalPerspectiveSection = document.getElementById(
      "historical-perspective-section"
    );
    historicalPerspectiveEl = document.getElementById("historical-perspective");
  }

  function init() {
    cacheDomReferences();

    loadApiBaseUrl().then(function (apiBaseUrl) {
      forecastForm.addEventListener("submit", function (event) {
        handleSubmit(apiBaseUrl, event);
      });
      loadCities(apiBaseUrl);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose internals for potential testing without a DOM harness.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      stripTrailingSlash: stripTrailingSlash,
      extractErrorMessage: extractErrorMessage
    };
  }
})();
