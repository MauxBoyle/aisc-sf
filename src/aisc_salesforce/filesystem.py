"""Small, platform-aware filesystem durability helpers."""

import os
from os import PathLike


def sync_directory(path: str | PathLike[str]) -> None:
    """Persist directory metadata where directory handles are supported."""
    if os.name == "nt":
        return

    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
