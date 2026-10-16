"""Container health check: python -m mtdl_web.healthcheck (exit 0 = healthy)."""

import os
import sys
import urllib.error
import urllib.request


def drop_root() -> None:
    """Docker runs the health check as the image's user, root: become PUID:PGID first, like the app
    (docker/entrypoint.sh), so that no process keeps root once mtdl-web runs. Started as another user (user: in
    Compose) there is nothing to drop."""
    if os.getuid() != 0:
        return
    os.setgroups([])
    os.setgid(int(os.environ.get("PGID", "").strip() or 100))
    os.setuid(int(os.environ.get("PUID", "").strip() or 99))


def main() -> int:
    drop_root()
    port = os.environ.get("MTDL_PORT", "").strip() or "8080"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=4) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
