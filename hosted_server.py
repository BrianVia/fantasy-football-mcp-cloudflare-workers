"""Separate private-pilot HTTP entry point; requires explicit Auth0 configuration."""

import os
from src.hosted.auth import PilotSettings
from src.hosted.server import create_server


def prepare_hosted_environment():
    if any(key.startswith("YAHOO_") for key in os.environ):
        raise RuntimeError("Remove personal YAHOO_* variables from the hosted service")
    os.environ["PILOT_HOSTED_MODE"] = "1"


if __name__ == "__main__":
    prepare_hosted_environment()
    server = create_server(PilotSettings.from_env())
    server.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        path="/mcp",
        stateless_http=True,
        json_response=True,
        show_banner=False,
    )
