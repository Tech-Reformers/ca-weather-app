"""CDK stack for the CA Weather App.

This stack defines the full serverless topology for the CA Weather App:

* Task 13.1 -- the Backend_Service Lambda function and its IAM permission to
  invoke Amazon Bedrock.
* Task 13.2 -- the API Gateway HTTP API (``GET /cities`` / ``POST /forecast``)
  that fronts the Lambda, the S3 static website bucket that hosts the
  Frontend_SPA, the deployment that uploads the SPA assets (injecting the
  deployed API URL via a generated ``config.json``), and the ``CfnOutput``
  values for the website and API URLs.

The Backend_Service is the Python AWS Lambda function described in the design:
it validates the requested city, fetches real Weather_Data from the NWS API over
the public internet, and invokes the Amazon Bedrock Converse API to summarize
that data. Calling the Converse operation requires the ``bedrock:InvokeModel``
IAM action on the function's execution role, which is granted below.
"""

from __future__ import annotations

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

# Path to the backend Lambda source, resolved relative to this stack file so it
# works regardless of the directory CDK is invoked from. The backend lives at
# ../backend from this infra/ directory.
_BACKEND_ASSET_PATH = os.path.join(os.path.dirname(__file__), "..", "backend")

# Path to the Frontend_SPA static assets (index.html, app.js, styles.css,
# config.json), resolved relative to this stack file. The frontend lives at
# ../frontend from this infra/ directory.
_FRONTEND_ASSET_PATH = os.path.join(os.path.dirname(__file__), "..", "frontend")

# Placeholder defaults so `cdk synth` works without any context being supplied.
# These should be overridden at deploy time (see below) with real values:
#   - BEDROCK_MODEL_ID: the Bedrock model id to invoke (model access must also
#     be enabled for it in the Bedrock console).
#   - NWS_USER_AGENT_CONTACT: a real contact (email or URL) for the descriptive
#     User-Agent the NWS API requires on every request.
_DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_DEFAULT_NWS_USER_AGENT_CONTACT = "ca-weather-app@example.com"


class CaWeatherAppStack(Stack):
    """Infrastructure for the CA Weather App.

    Defines the Backend_Service Lambda function (Task 13.1), then the HTTP API,
    the S3 static website bucket, the SPA deployment, and the stack outputs
    (Task 13.2). The Lambda is stored as ``self.forecast_function`` so the HTTP
    API integration can reference it.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Resolve the Bedrock model id and NWS contact from CDK context or the
        # environment, falling back to placeholder defaults so synth always
        # succeeds. Set real values at deploy time, e.g.:
        #   cdk deploy -c bedrock_model_id=... -c nws_user_agent_contact=...
        # or export BEDROCK_MODEL_ID / NWS_USER_AGENT_CONTACT in the environment.
        bedrock_model_id = (
            self.node.try_get_context("bedrock_model_id")
            or os.environ.get("BEDROCK_MODEL_ID")
            or _DEFAULT_BEDROCK_MODEL_ID
        )
        nws_user_agent_contact = (
            self.node.try_get_context("nws_user_agent_contact")
            or os.environ.get("NWS_USER_AGENT_CONTACT")
            or _DEFAULT_NWS_USER_AGENT_CONTACT
        )

        # Backend_Service Lambda (Requirements 7.1, 7.2, 7.4).
        #
        # Not attached to a VPC: a Lambda function that is not placed in a VPC
        # has outbound internet access by default, which is exactly what the
        # Backend_Service needs to reach the public api.weather.gov and the
        # Amazon Bedrock endpoints. Attaching it to a VPC would require a NAT
        # gateway for that egress, so we deliberately leave `vpc` unset.
        #
        # boto3 is provided by the Lambda runtime, so the backend code can
        # import it without bundling it into the asset.
        self.forecast_function = _lambda.Function(
            self,
            "ForecastFunction",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(_BACKEND_ASSET_PATH),
            # ~29s to stay within the API Gateway 30s budget (Requirement 7.4).
            timeout=Duration.seconds(29),
            environment={
                "BEDROCK_MODEL_ID": bedrock_model_id,
                "NWS_USER_AGENT_CONTACT": nws_user_agent_contact,
            },
        )

        # Grant permission to invoke Bedrock models via the Converse API
        # (Requirement 3.1). Current Anthropic models are invoked through a
        # cross-region system-defined INFERENCE PROFILE (e.g.
        # ``us.anthropic.claude-haiku-4-5-...``) rather than a bare
        # foundation-model id, so the role needs InvokeModel on BOTH the
        # inference-profile ARN and the underlying foundation models. Because a
        # cross-region profile fans out to foundation models in several regions,
        # the foundation-model resource is scoped across all regions/accounts.
        self.forecast_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*:*:foundation-model/*",
                    "arn:aws:bedrock:*:*:inference-profile/*",
                ],
            )
        )

        # ------------------------------------------------------------------
        # API_Endpoint: API Gateway HTTP API (Requirements 7.2, 7.3, 1.2).
        # ------------------------------------------------------------------
        #
        # An HTTP API (v2) is used rather than a REST API: lower cost/latency
        # and simpler configuration for the two JSON routes (design: "API
        # Gateway HTTP API (not REST API)"). A single Lambda proxy integration
        # backs both routes; the Backend_Service inspects the method/path to
        # dispatch GET /cities vs POST /forecast.
        #
        # CORS note (direct-origin baseline): the SPA is served from the S3
        # website origin and calls this API cross-origin. The S3 website URL is
        # a deploy-time token and is not known when the CORS policy is
        # declared, so preflight allows any origin ("*"). This is acceptable
        # for this public, read-only demo -- the Backend_Service also sets
        # Access-Control-Allow-Origin/Methods/Headers on every response. A
        # production enhancement (noted in the design) would front both the SPA
        # and the API with CloudFront so they share one origin and the wildcard
        # is unnecessary.
        http_integration = apigwv2_integrations.HttpLambdaIntegration(
            "ForecastIntegration", self.forecast_function
        )

        self.http_api = apigwv2.HttpApi(
            self,
            "WeatherHttpApi",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[
                    apigwv2.CorsHttpMethod.GET,
                    apigwv2.CorsHttpMethod.POST,
                    apigwv2.CorsHttpMethod.OPTIONS,
                ],
                allow_headers=["Content-Type"],
            ),
        )

        # GET /cities -> supported-city catalog (Requirements 1.1, 1.2, 5.1).
        self.http_api.add_routes(
            path="/cities",
            methods=[apigwv2.HttpMethod.GET],
            integration=http_integration,
        )
        # POST /forecast -> forecast request (Requirements 1.2, 7.2, 7.3).
        self.http_api.add_routes(
            path="/forecast",
            methods=[apigwv2.HttpMethod.POST],
            integration=http_integration,
        )

        # ------------------------------------------------------------------
        # Frontend_SPA hosting: S3 static website bucket (Requirement 6).
        # ------------------------------------------------------------------
        #
        # Static website hosting is enabled with index.html as the root
        # document (Requirement 6.1). index.html is also used as the error
        # document so unknown paths resolve back to the SPA (Requirement 6.4):
        # the SPA is single-page, so returning its entry document for an
        # unmatched path keeps client-side behavior intact rather than showing
        # a bare S3 error page.
        #
        # public_read_access + a relaxed BlockPublicAccess (block ACLs, but
        # allow public bucket *policies*) are required for S3 website hosting
        # to serve objects to anonymous browsers; CDK attaches the public-read
        # bucket policy automatically when public_read_access is true.
        #
        # removal_policy=DESTROY + auto_delete_objects=True are demo-only so the
        # stack (and its uploaded SPA objects) can be torn down cleanly with
        # `cdk destroy`; they should not be used for buckets holding data that
        # must survive stack deletion.
        self.site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            website_index_document="index.html",
            website_error_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=True,
                ignore_public_acls=True,
                block_public_policy=False,
                restrict_public_buckets=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ------------------------------------------------------------------
        # SPA deployment: upload the built assets + a deploy-time config.json.
        # ------------------------------------------------------------------
        #
        # Two sources are layered into the bucket:
        #   1. Source.asset(frontend) -- the static SPA files (index.html,
        #      app.js, styles.css, and the checked-in placeholder config.json).
        #   2. Source.json_data("config.json", {...}) -- regenerates
        #      config.json at deploy time with the REAL API URL. Because it is
        #      listed after the asset source, it overwrites the placeholder
        #      config.json shipped in the asset.
        #
        # self.http_api.url is a CDK token resolved at deploy time; json_data
        # serializes it into the uploaded config.json so the SPA (which reads
        # {"apiBaseUrl": ...} at runtime) targets the deployed API. The URL
        # already carries the stage and a trailing slash; the SPA strips the
        # trailing slash before composing /cities and /forecast.
        s3deploy.BucketDeployment(
            self,
            "DeploySpa",
            sources=[
                s3deploy.Source.asset(_FRONTEND_ASSET_PATH),
                s3deploy.Source.json_data(
                    "config.json", {"apiBaseUrl": self.http_api.url}
                ),
            ],
            destination_bucket=self.site_bucket,
        )

        # ------------------------------------------------------------------
        # Outputs: the S3 website URL and the API endpoint URL.
        # ------------------------------------------------------------------
        CfnOutput(
            self,
            "WebsiteUrl",
            value=self.site_bucket.bucket_website_url,
            description="S3 static website URL for the Frontend_SPA.",
        )
        CfnOutput(
            self,
            "ApiEndpoint",
            value=self.http_api.url or "",
            description="Base URL of the API_Endpoint (HTTP API).",
        )
