"""CA Weather App backend package.

Serverless Python AWS Lambda Backend_Service: validates a requested California
city, fetches real Weather_Data from the U.S. National Weather Service API,
invokes Amazon Bedrock to summarize that data with a Historical_Perspective,
and returns a structured Forecast or error envelope.
"""
