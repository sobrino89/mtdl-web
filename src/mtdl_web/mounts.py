"""Folders mtdl-web writes to must be mounted: a folder whose mount is missing lies inside the
container, and everything written there is gone with the next container (every update recreates it).

Inside a container, a folder on the same device as the container's root file system is not mounted. Outside
containers (a plain install) there is nothing to check.
"""

from pathlib import Path

CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))  # Docker, Podman


def in_container() -> bool:
    return any(marker.exists() for marker in CONTAINER_MARKERS)


def unmounted(path: Path, root: Path = Path("/")) -> bool:
    """True if ``path``, or the nearest folder above it that exists, is on the device of ``root``."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return probe.stat().st_dev == root.stat().st_dev
    except OSError:
        return False
