"""Separate private-pilot HTTP entry point; requires explicit Auth0 configuration."""

import os
from src.hosted.auth import PilotSettings
from src.hosted.server import create_server

if __name__ == "__main__":
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
