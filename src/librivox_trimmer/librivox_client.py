from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from librivox_trimmer.progress import DownloadProgress


USER_AGENT = "LibriVoxAudiobookTrimmer/0.1 (chapter archive downloader)"


def find_archive_url(page_html: str, page_url: str, quality: str = "best") -> str:
    if quality not in ("best", "smaller"):
        raise ValueError("Unsupported download quality.")
    soup = BeautifulSoup(page_html, "html.parser")
    candidates: list[tuple[int, str]] = []
    for anchor in soup.find_all("a", href=True):
        absolute_url = urljoin(page_url, anchor["href"])
        parsed = urlparse(absolute_url)
        host = (parsed.hostname or "").lower()
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
) -> Path:
    response = requests.get(
        project_url,
        headers={"User-Agent": USER_AGENT},
        timeout=(10, 45),
    )
    response.raise_for_status()
    archive_url = find_archive_url(response.text, response.url, quality)
    archive_response = requests.get(
        archive_url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=(10, 60),
    )
    archive_response.raise_for_status()
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