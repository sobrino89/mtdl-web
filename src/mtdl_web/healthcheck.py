"""Container health check: python -m mtdl_web.healthcheck (exit 0 = healthy)."""

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    port = os.environ.get("MTDL_PORT", "").strip() or "8080"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=4) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
