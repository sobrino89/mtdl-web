"""Start the server: python -m mtdl_web. Settings come from the environment (mtdl_web.config)."""

import logging
import re

import uvicorn

from mtdl_web.app import create_app
from mtdl_web.config import Settings

# Polled by the container health check (every minute) and by every open UI (every few seconds): not worth a line.
QUIET_PATHS = ("/api/health", "/api/v1/status")


class RedactKeys(logging.Filter):
    """httpx logs every request URL; a TMDB v3 key can only travel as query parameter (metadata.py)."""

    _KEY = re.compile(r"(api_key=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "api_key=" in message:
            record.msg, record.args = self._KEY.sub(r"\1***", message), None
        return True


class QuietPolling(logging.Filter):
    """Drops access log lines of successful polling requests; errors on those paths still show."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:  # uvicorn: client, method, path, http version, status
            path, status = str(args[2]).split("?", 1)[0], args[4]
            if path in QUIET_PATHS and isinstance(status, int) and status < 400:
                return False
        return True


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("uvicorn.access").addFilter(QuietPolling())
    logging.getLogger("httpx").addFilter(RedactKeys())
    # The MCP SDK reports every stateless request at INFO; the access log shows /mcp already.
    logging.getLogger("mcp").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level,
        proxy_headers=False,
    )


# Worker processes start through a fork server and import this module again: keep the guard.
if __name__ == "__main__":
    main()
