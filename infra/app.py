#!/usr/bin/env python3
"""CDK v2 app entry point for the CA Weather App.

Instantiates the single ``CaWeatherAppStack``. Run with the AWS CDK CLI
(`cdk synth`, `cdk deploy`); `cdk.json` points the CLI at `python app.py`.
"""

import aws_cdk as cdk

from ca_weather_app_stack import CaWeatherAppStack

app = cdk.App()

CaWeatherAppStack(app, "CaWeatherAppStack")

app.synth()
