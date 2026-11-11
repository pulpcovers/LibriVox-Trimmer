from copy import deepcopy
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote, urlparse

from mutagen.mp3 import MP3


def normalize_chapter_title(title: str) -> str:
    title = " ".join(unicodedata.normalize("NFKC", title).split())
    roman = r"(?=[ivxlcdm]+\b)m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
    number_words = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
    number = rf"(?:\d{{1,3}}|{roman}|{number_words}(?:[- ]{number_words})*)"
    title = re.sub(rf"\s+\((?:chapter|section)\s+{number}\)\s*$", "", title, flags=re.IGNORECASE)
    separator = r"(?:\s*[:.,\-\u2013\u2014]\s*|\s+)"
    labelled = re.match(
        rf"^(?:(?:part|book|volume)\s+{number}{separator})?"
        rf"(?:chapter|section|ch\.?)\s+{number}{separator}(?P<title>\S.*)$",
        title, re.IGNORECASE,
    )
    if labelled:
        return labelled.group("title").strip()
    compound = re.match(r"^\d{1,3}\s*[-.]\s*\d{1,3}\s*[.:\-\u2013\u2014]\s+(\S.*)$", title)
    if compound:
        return compound.group(1).strip()
    indexed = re.match(rf"^(?:\d{{1,3}}|{roman})\s*[.:\-\u2013\u2014]\s+(\S.*)$", title, re.IGNORECASE)
    return indexed.group(1).strip() if indexed else title


def normalize_narrator_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(
        r"\s*\(\s*(?:(?:b(?:orn)?|d(?:ied)?)\.?\s*)?[12]\d{3}"
        r"(?:\s*[-\u2013\u2014]\s*(?:[12]\d{3})?)?\s*\)\s*$",
        "", name, flags=re.IGNORECASE,
    )
    name = name.replace("\u2019", "'").replace("\u2018", "'").replace("\u2011", "-")
    name = re.sub(r"\b([^\W\d_])\.(?=[^\W\d_]|\s|$)", r"\1 ", name)
    return " ".join(name.split())


def catalog_chapter(path: Path, book_metadata: dict | None) -> dict:
    matches = [
        chapter for chapter in (book_metadata or {}).get("chapters", [])
        if any(
            Path(unquote(urlparse(url).path)).name.casefold() == path.name.casefold()
            for url in chapter.get("audio_urls", [])
        )
    ]
    return matches[0] if len(matches) == 1 else {}


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