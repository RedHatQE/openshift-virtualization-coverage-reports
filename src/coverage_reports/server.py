"""Simple HTTP file server for serving generated reports.

Uses Python's built-in ``http.server`` module. Configurable port
and directory.
"""

from __future__ import annotations

import logging
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class QuietHandler(SimpleHTTPRequestHandler):
    """HTTP handler that logs to the application logger instead of stderr."""

    def log_message(self, format: str, *args: object) -> None:
        """Route access logs through the application logger.

        Args:
            format: Log format string.
            args: Format arguments.
        """
        LOGGER.info(format % args)


def start_server(directory: Path, port: int = 8080) -> None:
    """Start the HTTP file server.

    Serves static files from the given directory. Blocks until
    interrupted (Ctrl+C or SIGTERM).

    Args:
        directory: Directory to serve files from.
        port: Port number to listen on.
    """
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        LOGGER.warning(f"Created missing directory: {directory}")

    handler = partial(QuietHandler, directory=str(directory))
    server = HTTPServer(server_address=("0.0.0.0", port), RequestHandlerClass=handler)

    LOGGER.info(f"Serving {directory} on port {port}")
    print(f"Serving reports from {directory} at http://0.0.0.0:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Server shutdown requested")
    finally:
        server.server_close()
        LOGGER.info("Server stopped")
