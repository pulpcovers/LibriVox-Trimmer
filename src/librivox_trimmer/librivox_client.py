from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString

from librivox_trimmer.progress import DownloadProgress


USER_AGENT = "LibriVoxAudiobookTrimmer/0.1 (chapter archive downloader)"


def _require_https(url: str, what: str) -> None:
    if urlparse(url).scheme.lower() != "https":
        raise ValueError(f"The {what} must use https, not an unencrypted connection.")


def parse_project_metadata(page_html: str, page_url: str) -> dict | None:
    soup = BeautifulSoup(page_html, "html.parser")
    title = next((heading.get_text(" ", strip=True) for heading in soup.find_all("h1")
                  if not {"logo", "assistive-text"}.intersection(heading.get("class", []))), "")
    chapters = []
    for row in soup.select("table tr"):
        audio_urls = list(dict.fromkeys(
            urljoin(page_url, anchor["href"])
            for anchor in row.find_all("a", href=True)
            if urlparse(urljoin(page_url, anchor["href"])).path.lower().endswith(".mp3")
        ))
        if not audio_urls:
            continue
        readers = {}
        for anchor in row.find_all("a", href=True):
            reader_url = urlparse(urljoin(page_url, anchor["href"]))
            match = re.fullmatch(r"/reader/(\d+)/?", reader_url.path)
            if reader_url.hostname in {"librivox.org", "www.librivox.org"} and match:
                readers[match.group(1)] = {"id": match.group(1), "name": anchor.get_text(" ", strip=True)}
        chapter_name = row.select_one(".chapter-name")
        cells = row.find_all("td")
        chapter_number = "".join(cells[0].find_all(string=True, recursive=False)).strip() if cells else ""
        chapters.append({
            "number": chapter_number,
            "title": chapter_name.get_text(" ", strip=True) if chapter_name else "",
            "audio_urls": audio_urls,
            "readers": list(readers.values()),
        })
    if not title or not chapters:
        return None
    author_names = []
    translator_names = []
    for paragraph in soup.select(".book-page-author"):
        credits = author_names
        for child in paragraph.children:
            if isinstance(child, NavigableString):
                if "translated by" in child.casefold():
                    credits = translator_names
            elif child.name == "a" and re.fullmatch(r"/author/\d+/?", urlparse(child.get("href", "")).path):
                credits.append(" ".join(child.find_all(string=True, recursive=False)).strip())
    language = ""
    for field in soup.select(".book-page-genre"):
        label = field.find("span")
        if label and label.get_text(strip=True) == "Language:":
            language = field.get_text(" ", strip=True).removeprefix("Language:").strip()
    reader_ids = {reader["id"] for chapter in chapters for reader in chapter["readers"]}
    complete = all(chapter["readers"] for chapter in chapters)
    narration = "multiple" if len(reader_ids) > 1 else "single" if complete and reader_ids else "unknown"
    return {
        "schema_version": 1,
        "source_url": page_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "authors": author_names,
        "translators": translator_names,
        "language": language,
        "chapters": chapters,
        "reader_count": len(reader_ids),
        "reader_metadata_complete": complete,
        "narration": narration,
    }


def fetch_project_metadata(project_url: str) -> dict | None:
    _require_https(project_url, "project address")
    if urlparse(project_url).hostname not in {"librivox.org", "www.librivox.org"}:
        raise ValueError("Audiobook metadata must be fetched from a LibriVox project URL.")
    response = requests.get(project_url, headers={"User-Agent": USER_AGENT}, timeout=(10, 45))
    try:
        response.raise_for_status()
        _require_https(response.url, "project page (it was redirected)")
        if urlparse(response.url).hostname not in {"librivox.org", "www.librivox.org"}:
            raise ValueError("The metadata request was redirected away from LibriVox.")
        return parse_project_metadata(response.text, response.url)
    finally:
        response.close()


def find_archive_url(page_html: str, page_url: str, quality: str = "best") -> str:
    if quality not in ("best", "smaller"):
        raise ValueError("Unsupported download quality.")
    soup = BeautifulSoup(page_html, "html.parser")
    candidates: list[tuple[int, str]] = []
    for anchor in soup.find_all("a", href=True):
        absolute_url = urljoin(page_url, anchor["href"])
        parsed = urlparse(absolute_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() != "https":
            continue
        if host != "archive.org" and not host.endswith(".archive.org"):
            continue
        if not parsed.path.lower().endswith(".zip"):
            continue
        path = parsed.path.lower()
        preferred = "64kb" if quality == "smaller" else "128kb"
        preference = 2 if preferred in path else 1 if "kb" in path else 0
        candidates.append((preference, absolute_url))

    if not candidates:
        raise ValueError("No Archive.org chapter ZIP link was found on that page.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def download_project_archive(
    project_url: str,
    destination: Path,
    *,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    quality: str = "best",
    metadata_callback: Callable[[dict | None], None] | None = None,
) -> Path:
    _require_https(project_url, "project address")
    response = requests.get(
        project_url,
        headers={"User-Agent": USER_AGENT},
        timeout=(10, 45),
    )
    response.raise_for_status()
    _require_https(response.url, "project page (it was redirected)")
    if metadata_callback is not None:
        metadata_callback(parse_project_metadata(response.text, response.url))
    archive_url = find_archive_url(response.text, response.url, quality)
    archive_response = requests.get(
        archive_url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=(10, 60),
    )
    try:
        archive_response.raise_for_status()
        _require_https(archive_response.url, "download (it was redirected)")
    except Exception:
        archive_response.close()
        raise
    content_length = archive_response.headers.get("content-length")
    try:
        total_bytes = int(content_length) if content_length is not None else None
    except ValueError:
        total_bytes = None
    if total_bytes is not None and total_bytes <= 0:
        total_bytes = None

    filename = Path(unquote(urlparse(archive_url).path)).name or "chapters.zip"
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / filename
    try:
        downloaded_bytes = 0
        if progress_callback is not None:
            progress_callback(DownloadProgress(downloaded_bytes, total_bytes))
        with archive_path.open("wb") as output:
            for chunk in archive_response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
                    downloaded_bytes += len(chunk)
                    if progress_callback is not None:
                        progress_callback(DownloadProgress(downloaded_bytes, total_bytes))
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    finally:
        archive_response.close()
    return archive_path