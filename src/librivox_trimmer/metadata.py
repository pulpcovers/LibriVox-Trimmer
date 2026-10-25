from copy import deepcopy
from pathlib import Path

from mutagen.mp3 import MP3


def read_track(path: Path) -> tuple[float, str, str]:
    audio = MP3(path)
    if audio.info is None:
        raise ValueError(f"Could not read MP3 duration: {path.name}")
    title = str(audio.tags.get("TIT2", path.stem)) if audio.tags else path.stem
    artist = str(audio.tags.get("TPE1", "")) if audio.tags else ""
    return float(audio.info.length), title, artist


def restore_id3_tags(source_path: Path, output_path: Path) -> None:
    source = MP3(source_path)
    if source.tags is None:
        return
    output = MP3(output_path)
    output.tags = deepcopy(source.tags)
    output.save(v2_version=4)