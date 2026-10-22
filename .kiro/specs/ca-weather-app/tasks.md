# Implementation Plan: CA Weather App

## Overview

This plan implements the CA Weather App as a serverless system: a Python AWS Lambda Backend_Service (validation, real Weather_Data fetch from the NWS API, Amazon Bedrock summarization with a Historical_Perspective), a static S3-hosted Frontend_SPA with PG&E-inspired demo branding, and AWS CDK v2 (Python) infrastructure.

The build order favors the pure, dependency-injected core first (so it can be property-tested without touching AWS or api.weather.gov), then the external-service wrappers, then the Lambda handler, then the frontend, then the CDK stack that wires everything together. Property-based tests use Hypothesis and map one-to-one to the nine Correctness Properties in the design. Test sub-tasks are marked optional with `*`.

## Tasks

- [x] 1. Set up the backend project structure and dependencies
  - Create the backend package directory with empty modules: `handler.py`, `cities.py`, `validation.py`, `weather_client.py`, `forecast.py`, `bedrock_client.py`, and a `models.py` for shared dataclasses
  - Create a `tests/` directory for unit and property tests
  - Add project/dependency config (e.g. `pyproject.toml` or `requirements-dev.txt`) declaring `pytest`, `hypothesis`, and `boto3`
  - Configure Hypothesis to run a minimum of 100 iterations by default
  - _Requirements: 7.1_

- [x] 2. Define core data models and internal result types
  - [x] 2.1 Implement the `City`, `WeatherData`, and `Forecast` data models
    - `City` frozen dataclass with `id`, `name`, `lat`, `lon`
    - `WeatherData` frozen dataclass with `city`, `temperature`, `temperature_unit`, `conditions`, `wind`, `detailed_forecast`, `retrieved_at`
    - `Forecast` frozen dataclass with `summary` and `historical_perspective`
    - Internal result types: `ValidationResult` (INVALID | WELL_FORMED(city_id)), `ContentResult` (OK | UNAVAILABLE), `ForecastResult` (Success | Error(code, message)), and a `WeatherUnavailable` exception
    - _Requirements: 2.6, 5.4, 1.3, 4.6_

- [x] 3. Implement the supported-city catalog (cities.py)
  - [x] 3.1 Create the California city catalog with coordinates and lookup helpers
    - Define the fixed catalog with Los Angeles, San Francisco, San Diego, Sacramento, San Jose, Fresno, Oakland, Long Beach, each carrying `lat`/`lon`
    - Implement `supported_cities() -> list[City]`, `get_city(city_id) -> City | None`, and `is_supported_city(city_id) -> bool`
    - _Requirements: 5.1, 5.2, 2.1_

  - [ ]* 3.2 Write unit tests for the catalog
    - `get_city` returns the expected `City` (with coordinates) for a known id and `None` for an unknown id; `supported_cities` returns the full catalog
    - _Requirements: 5.1, 5.2_

- [x] 4. Implement pure validation functions (validation.py)
  - [x] 4.1 Implement city-value, content-bounds, and Historical_Perspective validation
    - `validate_city_value(raw)` — INVALID for null/non-string/empty/whitespace-only; otherwise WELL_FORMED with a trimmed, lowercased `city_id`
    - `validate_forecast_content(content)` — OK when `1 <= len <= 5000`, else UNAVAILABLE
    - `has_historical_perspective(result)` — true only when a non-empty Historical_Perspective is present
    - `truncate_historical_perspective(text)` — unchanged when `<= 1000` chars, else the first 1000 chars
    - _Requirements: 5.4, 3.5, 3.6, 4.4, 4.6_

  - [ ]* 4.2 Write unit tests for validation edge cases
    - Each INVALID sub-case; summary boundaries 0, 1, 5000, 5001; Historical_Perspective truncation boundaries 999, 1000, 1001; present/absent Historical_Perspective
    - _Requirements: 5.4, 3.5, 4.4, 4.6_

  - [ ]* 4.3 Write property test for normalization idempotence
    - **Property 3: Normalization is idempotent**
    - `# Feature: ca-weather-app, Property 3: Normalization is idempotent`
    - Assert `normalize(normalize(x)) == normalize(x)` for any string; min 100 iterations
    - **Validates: Requirements 5.1, 5.2**

  - [ ]* 4.4 Write property test for content-bounds classification
    - **Property 6: Content-bounds classification**
    - `# Feature: ca-weather-app, Property 6: Content-bounds classification`
    - For any summary string, OK iff `1 <= len <= 5000`, else UNAVAILABLE; emphasize boundary lengths 0, 1, 5000, 5001; min 100 iterations
    - **Validates: Requirements 3.4, 3.5**

  - [ ]* 4.5 Write property test for Historical_Perspective truncation
    - **Property 9: Historical_Perspective is truncated to at most 1000 characters**
    - `# Feature: ca-weather-app, Property 9: Historical_Perspective is truncated to at most 1000 characters`
    - Result unchanged when `<= 1000`; exactly first 1000 chars when longer; result length `== min(len, 1000)`; straddle 999/1000/1001; min 100 iterations
    - **Validates: Requirements 4.6**

- [x] 5. Implement the forecast orchestration (forecast.py)
  - [x] 5.1 Implement `generate_forecast` with injected weather_client and generator
    - Order: `validate_city_value` → INVALID_CITY on failure; `is_supported_city` → CITY_NOT_SUPPORTED (no downstream calls); `get_city`; `weather_client.fetch(city)` → WEATHER_UNAVAILABLE on `WeatherUnavailable` (generator NOT invoked); `generator.generate(city, weather)`; `validate_forecast_content` → FORECAST_UNAVAILABLE when out of bounds; `has_historical_perspective` → FORECAST_UNAVAILABLE when missing; `truncate_historical_perspective`; return Success
    - Both `weather_client` and `generator` are injected dependencies; the generator must receive exactly the `WeatherData` returned by the weather_client
    - _Requirements: 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 4.1, 4.2, 4.4, 5.3, 5.4_

  - [ ]* 5.2 Write property test for invalid city values
    - **Property 1: Null, empty, whitespace-only, or non-string values are INVALID_CITY**
    - `# Feature: ca-weather-app, Property 1: Null, empty, whitespace-only, or non-string values are INVALID_CITY`
    - Returns Error INVALID_CITY; neither injected weather_client nor generator invoked (use spies); min 100 iterations
    - **Validates: Requirements 5.4**

  - [ ]* 5.3 Write property test for unsupported cities
    - **Property 2: Unsupported well-formed city yields CITY_NOT_SUPPORTED without any downstream call**
    - `# Feature: ca-weather-app, Property 2: Unsupported well-formed city yields CITY_NOT_SUPPORTED without any downstream call`
    - Well-formed non-catalog id → Error CITY_NOT_SUPPORTED; neither dependency invoked (spies); min 100 iterations
    - **Validates: Requirements 1.4, 5.3**

  - [ ]* 5.4 Write property test for weather-fetch failure
    - **Property 4: Weather fetch failure yields WEATHER_UNAVAILABLE without invoking the generator**
    - `# Feature: ca-weather-app, Property 4: Weather fetch failure yields WEATHER_UNAVAILABLE without invoking the generator`
    - Fake weather_client raises `WeatherUnavailable` → Error WEATHER_UNAVAILABLE, no partial Forecast, generator NOT invoked (spy); min 100 iterations
    - **Validates: Requirements 2.3, 2.4, 2.5**

  - [ ]* 5.5 Write property test for dependency ordering and data provenance
    - **Property 5: The weather fetch and generator are reached only after validation passes, and the generator receives exactly the fetched Weather_Data**
    - `# Feature: ca-weather-app, Property 5: The weather fetch and generator are reached only after validation passes, and the generator receives exactly the fetched Weather_Data`
    - Spy weather_client/generator: weather_client invoked exactly once with the matching supported `City` only when both validations pass; generator receives exactly the returned `WeatherData`; neither invoked when validation fails; min 100 iterations
    - **Validates: Requirements 2.1, 2.2, 2.6**

  - [ ]* 5.6 Write property test for the success path
    - **Property 7: Supported city with real Weather_Data, a bounded summary, and a present Historical_Perspective yields Success**
    - `# Feature: ca-weather-app, Property 7: Supported city with real Weather_Data, a bounded summary, and a present Historical_Perspective yields Success`
    - Supported id + any fake WeatherData + summary length 1..5000 + non-empty HP → Success with matching `City`, that summary, and HP of at most 1000 chars; min 100 iterations
    - **Validates: Requirements 1.3, 4.1, 4.2**

  - [ ]* 5.7 Write property test for missing Historical_Perspective
    - **Property 8: Missing Historical_Perspective yields FORECAST_UNAVAILABLE with no partial Forecast**
    - `# Feature: ca-weather-app, Property 8: Missing Historical_Perspective yields FORECAST_UNAVAILABLE with no partial Forecast`
    - Supported city + good weather + in-bounds summary but empty/absent HP → Error FORECAST_UNAVAILABLE, no partial Forecast; min 100 iterations
    - **Validates: Requirements 4.4**

- [x] 6. Checkpoint - pure core complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement the Weather_Data_Service client (weather_client.py)
  - [x] 7.1 Implement the two-step NWS fetch over urllib.request
    - `fetch(city)`: `GET https://api.weather.gov/points/{lat},{lon}`, read `properties.forecast`, then `GET` that gridpoint forecast URL; map the first/current period into `WeatherData` (`temperature`, `temperatureUnit`, `shortForecast`, `windSpeed`+`windDirection`, `detailedForecast`) plus an ISO-8601 `retrieved_at`
    - Send a descriptive `User-Agent` header (app name + contact) on every request
    - Enforce a 10-second budget across both calls; raise `WeatherUnavailable` on timeout, error status, unreachable service, or malformed/empty response; build `WeatherData` only from the response (no fabricated/default values)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ]* 7.2 Write unit tests for weather_client with the HTTP layer mocked
    - Stub `urllib.request`: confirm the two-step call pattern and the `User-Agent` header; a well-formed response maps into `WeatherData`; timeouts, error statuses, and malformed/empty bodies surface as `WeatherUnavailable`; never calls the live service
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [x] 8. Implement the AI_Forecast_Generator client (bedrock_client.py)
  - [x] 8.1 Implement the Bedrock Converse wrapper
    - Build the Converse request: `modelId` from config, one `user` message whose text embeds the city name and the serialized `WeatherData`, a `system` prompt to ground the summary strictly in that data and to include a Historical_Perspective, and `inferenceConfig.maxTokens` sized to keep output within 5000 chars
    - Invoke the boto3 `bedrock-runtime` `converse` operation; read `response["output"]["message"]["content"][0]["text"]` and split it into `summary` and `historical_perspective`
    - _Requirements: 3.1, 3.2, 3.3, 4.1_

  - [ ]* 8.2 Write unit tests for bedrock_client with boto3 mocked
    - Stub `converse`: confirm the prompt embeds the `WeatherData`, requests both a summary and a Historical_Perspective, builds `messages`/`inferenceConfig` correctly, and extracts/splits the response text; never invokes the live service
    - _Requirements: 3.1, 3.2, 4.1_

- [x] 9. Implement the Lambda handler and HTTP response contract (handler.py)
  - [x] 9.1 Implement routing, timeouts, and the error-code→HTTP mapping
    - Parse the API Gateway proxy event; route `GET /cities` (return the catalog) and `POST /forecast` (call `generate_forecast` with the real weather_client and bedrock generator)
    - Enforce the internal 10s weather-fetch and 15s AI-invocation timeouts
    - Map result codes to HTTP: INVALID_CITY→400, CITY_NOT_SUPPORTED→404, WEATHER_UNAVAILABLE→503, FORECAST_UNAVAILABLE→502, FORECAST_TIMEOUT→504, INTERNAL_ERROR→500; success→200
    - Return the Lambda proxy output format (`statusCode`, `headers`, `body`, `isBase64Encoded`) with CORS headers (`Access-Control-Allow-Origin/Methods/Headers`) on every response; never return partial Forecast data on error
    - Wrap handling in a top-level guard that converts any unexpected exception into INTERNAL_ERROR (500)
    - _Requirements: 1.2, 1.4, 2.3, 3.4, 3.5, 4.4, 4.5, 5.3, 5.4, 7.2, 7.3, 7.4, 7.5, 8.5_

  - [ ]* 9.2 Write handler/integration tests for the proxy contract and error map
    - Representative proxy events: response conforms to the proxy output format with CORS headers present
    - `GET /cities` returns the catalog; `POST /forecast` returns the success envelope (`summary` + `historical_perspective`) and each error envelope including `WEATHER_UNAVAILABLE`; the top-level guard yields INTERNAL_ERROR (500); each code maps to the correct status
    - _Requirements: 1.2, 1.4, 2.3, 3.4, 4.4, 5.3, 5.4, 7.5, 8.5_

- [x] 10. Checkpoint - backend handler complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement the Frontend_SPA shell and request flow
  - [x] 11.1 Build the SPA structure, city loading, and forecast request handling
    - On load: `GET /cities` and populate a selection control from the response; restrict selection strictly to the fetched catalog and match entries against it
    - On submit: `POST /forecast` with the selected city; show a loading indicator and disable further submissions while in flight; apply a client-side timeout within the 30s budget
    - On success: render the forecast summary and the Historical_Perspective as a distinct, labeled section; remove the loading indicator and clear any prior error
    - On error or client timeout: stop the loading indicator and show an error message; retain the current selection and any previously displayed forecast
    - Read the deployed API base URL from build-time substitution or a runtime `config.json`
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 4.3, 5.1, 5.2, 8.1, 8.2, 8.3, 8.4_

- [x] 12. Implement the demo branding and Brand_Theme
  - [x] 12.1 Add the Demo_Banner, text wordmark, and themed layout
    - Fixed/sticky top Demo_Banner containing the text `DEMO`, visible without scrolling; offset main content (e.g. `padding-top` = `--demo-banner-height`) so the selection control and results are never obstructed
    - Use a text-only wordmark (e.g. "CA Weather — Demo"); do NOT embed, reference, hotlink, or display the official PG&E logo or any copyrighted/trademarked PG&E asset, and pull nothing from pge.com
    - Define the Brand_Theme as `:root` CSS custom properties (dark navy/near-black primary + yellow/gold accent) with a clean utility-style header/footer; apply the same palette consistently across the city selector, forecast summary, and Historical_Perspective section
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 12.2 Apply accessibility requirements to the theme and regions
    - Ensure every text/background pairing meets WCAG AA contrast (4.5:1 normal, 3:1 large text)
    - Expose the Demo_Banner to assistive tech (landmark `role` + `aria-label` such as "Demo environment banner" with literal `DEMO` in the accessible name)
    - Expose the Historical_Perspective as a labeled region (`section`/`region` named via `aria-labelledby` tied to its visible heading)
    - _Requirements: 9.1, 4.3, 9.5_

- [x] 13. Implement the AWS CDK v2 (Python) infrastructure
  - [x] 13.1 Create the CDK app and the Backend_Service Lambda with IAM
    - Create the CDK app (`app.py`) and `CaWeatherAppStack`
    - Add `aws_lambda.Function` (Python runtime, handler = `handler.py`, ~29s timeout, NOT attached to a VPC) with env vars `BEDROCK_MODEL_ID` and `NWS_USER_AGENT_CONTACT`
    - Grant `bedrock:InvokeModel` via an `aws_iam.PolicyStatement` on the function role scoped to `arn:aws:bedrock:*:*:foundation-model/*`
    - _Requirements: 7.1, 7.2, 7.4, 3.1_

  - [x] 13.2 Add the HTTP API, S3 static website hosting, SPA deployment, and outputs
    - `aws_apigatewayv2.HttpApi` with `GET /cities` and `POST /forecast` routes via `HttpLambdaIntegration`, and CORS allowing the S3 origin, GET/POST, and `Content-Type`
    - `aws_s3.Bucket` configured for static website hosting with index (root) and error documents
    - `aws_s3_deployment.BucketDeployment` to upload the built SPA assets, with the deployed API URL supplied to the SPA (build-time substitution or generated `config.json`)
    - `CfnOutput` for the S3 website URL and the API endpoint URL
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 7.2, 7.3, 1.1, 1.2_

  - [ ]* 13.3 Write cdk synth assertion/snapshot tests for the stack
    - Over the synthesized template, assert: Lambda (Python runtime, ~29s timeout, expected env vars, no VPC attachment); the `bedrock:InvokeModel` IAM policy statement; the HTTP API with both routes and CORS; the S3 website bucket with index/error docs and the SPA `BucketDeployment`
    - _Requirements: 6.1, 6.4, 7.1, 7.2_

- [x] 14. Deploy the stack
  - Run `cdk bootstrap` once per account/region, then `cdk synth` to validate, then `cdk deploy` to provision the Lambda, HTTP API, S3 bucket, and SPA upload; capture the website and API endpoint `CfnOutput` values
  - Enable model access for the chosen model in the Amazon Bedrock console (required in addition to the IAM permission)
  - _Requirements: 6.1, 6.2, 7.1, 7.2, 3.1_

- [x] 15. Final checkpoint - full system wired
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (unit, property, and infrastructure tests) and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each property test maps one-to-one to a Correctness Property in the design, is tagged with `# Feature: ca-weather-app, Property N: ...`, and runs a minimum of 100 iterations with a fake/spy weather_client and generator — never calling api.weather.gov or Bedrock.
- The pure core (cities, validation, forecast orchestration) is implemented and property-tested before the external-service wrappers so broad input coverage runs without AWS or network access.
- Checkpoints validate incrementally: after the pure core, after the backend handler, and a final checkpoint once the SPA and CDK stack are wired.
- Each task references specific requirement sub-clauses for traceability.
- The frontend uses an original PG&E-inspired theme only: a text wordmark and generic CSS, with no copyrighted or trademarked PG&E assets and nothing sourced from pge.com.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1"] },
    { "id": 1, "tasks": ["3.1", "4.1"] },
    { "id": 2, "tasks": ["3.2", "4.2", "4.3", "4.4", "4.5", "5.1"] },
    { "id": 3, "tasks": ["5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "7.1", "8.1"] },
    { "id": 4, "tasks": ["7.2", "8.2", "9.1"] },
    { "id": 5, "tasks": ["9.2", "11.1", "13.1"] },
    { "id": 6, "tasks": ["12.1", "13.2"] },
    { "id": 7, "tasks": ["12.2", "13.3"] }
  ]
}
```
