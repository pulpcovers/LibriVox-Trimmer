import shutil
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

import portalocker


MANAGED_MARKER = ".librivox-trimmer-managed"


def mark_managed_directory(directory: Path) -> None:
    (directory / MANAGED_MARKER).write_text("Created by LibriVox Trimmer.\n", encoding="utf-8")


def is_managed_assets_dir(directory: Path) -> bool:
    """True for a folder this app created: it has the marker, or the layout of an older download."""
    if directory.is_symlink() or not directory.is_dir():
        return False
    if (directory / MANAGED_MARKER).is_file():
        return True
    for entry in directory.iterdir():
        if entry.is_symlink():
            return False
        if entry.is_dir() and entry.name == "chapters":
            if any(item.is_symlink() or (item.is_file() and item.suffix.lower() != ".mp3")
                   for item in entry.rglob("*")):
                return False
        elif not (entry.is_file() and entry.suffix.lower() == ".zip"):
            return False
    return True


def _lock_path(projects_directory: Path, assets_directory: Path) -> Path:
    return projects_directory / f".{assets_directory.name}.lock"


@contextmanager
def lock_download_directory(
    projects_directory: Path,
    assets_directory: Path,
) -> Iterator[None]:
    projects_directory.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(projects_directory, assets_directory)
    with portalocker.Lock(str(lock_path), mode="a", timeout=0):
        yield


def cleanup_incomplete_downloads(projects_directory: Path) -> list[Path]:
    """Remove orphaned project asset folders, but never touch active downloads."""
    if not projects_directory.is_dir():
        return []

    removed: list[Path] = []
    for assets_directory in projects_directory.glob("*_files"):
        if not is_managed_assets_dir(assets_directory):
            continue
        project_name = assets_directory.name.removesuffix("_files")
        project_path = projects_directory / f"{project_name}.lvproj"
        if project_path.exists():
            continue

        try:
            with portalocker.Lock(
                str(_lock_path(projects_directory, assets_directory)),
                mode="a",
                timeout=0,
            ):
                if project_path.exists() or not assets_directory.exists():
                    continue
                shutil.rmtree(assets_directory)
                removed.append(assets_directory)
        except portalocker.exceptions.LockException:
            continue
        except OSError:
            continue
    return removed