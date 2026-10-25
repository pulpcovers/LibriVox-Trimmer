from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Track:
    path: Path
    duration_seconds: float
    title: str
    artist: str = ""


def natural_path_key(path: Path) -> tuple[tuple[tuple[int, int | str], ...], str]:
    """Sort filenames by numeric portions, so chapter 10 follows chapter 2."""
    name_parts = re.split(r"(\d+)", path.name.casefold())
    natural_parts = tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in name_parts
    )
    return natural_parts, path.as_posix().casefold()