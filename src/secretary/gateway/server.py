"""Uvicorn server wrapper for the message gateway.

Can run standalone (``python -m secretary.gateway.server``) or alongside
the Secretary daemon via ``secretary serve``.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def create_server(
    host: str = "0.0.0.0",
    port: int = 8900,
    log_level: str = "info",
) -> None:
    """Start the gateway uvicorn server.

    Args:
        host: Bind address.
        port: Bind port.
        log_level: Uvicorn log level.
    """
    import uvicorn

    logger.info("Starting gateway server on %s:%d", host, port)
    uvicorn.run(
        "secretary.gateway.api:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
    )


def main() -> None:
    """CLI entry point: python -m secretary.gateway.server."""
    import argparse

    parser = argparse.ArgumentParser(description="Secretary Gateway Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8900, help="Bind port (default: 8900)")
    parser.add_argument("--log-level", default="info", help="Log level (default: info)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    create_server(host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
