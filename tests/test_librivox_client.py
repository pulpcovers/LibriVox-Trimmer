import pytest

from librivox_trimmer import librivox_client
from librivox_trimmer.librivox_client import download_project_archive, find_archive_url
from librivox_trimmer.progress import DownloadProgress


def test_find_archive_url_prefers_128kb_mp3_zip():
    html = """
    <a href="https://archive.org/download/book/book_64kb_mp3.zip">64 kbps</a>
    <a href="https://archive.org/download/book/book_128kb_mp3.zip">128 kbps</a>
    """

    result = find_archive_url(html, "https://librivox.org/book/")

    assert result.endswith("book_128kb_mp3.zip")


def test_find_archive_url_requires_archive_zip():
    with pytest.raises(ValueError, match="No Archive.org"):
        find_archive_url('<a href="/book/">Book</a>', "https://librivox.org/book/")


def test_smaller_download_prefers_64kb():
    html = (
        '<a href="https://archive.org/download/book/book_128kb_mp3.zip">128</a>'
        '<a href="https://archive.org/download/book/book_64kb_mp3.zip">64</a>'
    )
    assert find_archive_url(html, "https://librivox.org/book/", "smaller").endswith("book_64kb_mp3.zip")


def test_find_archive_url_ignores_unencrypted_links():
    html = '<a href="http://archive.org/download/book/book_128kb_mp3.zip">ZIP</a>'

    with pytest.raises(ValueError, match="No Archive.org"):
        find_archive_url(html, "https://librivox.org/book/")


def test_download_refuses_http_addresses_and_downgrading_redirects(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="https"):
        download_project_archive("http://librivox.org/book/", tmp_path)

    monkeypatch.setattr(
        librivox_client.requests, "get",
        lambda *_a, **_k: type("R", (), {
            "url": "http://librivox.org/book/", "text": "", "raise_for_status": lambda self: None,
        })(),
    )
    with pytest.raises(ValueError, match="redirected"):
        download_project_archive("https://librivox.org/book/", tmp_path)


class FakeResponse:
    def __init__(self, *, url, text="", headers=None, chunks=()):
        self.url = url
        self.text = text
        self.headers = headers or {}
        self.chunks = chunks
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        assert chunk_size == 1024 * 1024
        yield from self.chunks

    def close(self):
        self.closed = True


def test_download_reports_bytes_and_total_when_content_length_is_known(tmp_path, monkeypatch):
    page = FakeResponse(
        url="https://librivox.org/book/",
        text='<a href="https://archive.org/download/book/book_128kb_mp3.zip">ZIP</a>',
    )
    archive = FakeResponse(
        url="https://archive.org/download/book/book_128kb_mp3.zip",
        headers={"content-length": "6"},
        chunks=(b"abc", b"def"),
    )
    responses = iter((page, archive))
    monkeypatch.setattr(librivox_client.requests, "get", lambda *_args, **_kwargs: next(responses))
    progress: list[DownloadProgress] = []

    result = download_project_archive(
        "https://librivox.org/book/",
        tmp_path,
        progress_callback=progress.append,
    )

    assert result.read_bytes() == b"abcdef"
    assert progress == [
        DownloadProgress(0, 6),
        DownloadProgress(3, 6),
        DownloadProgress(6, 6),
    ]
    assert archive.closed


def test_download_reports_received_bytes_when_content_length_is_missing(tmp_path, monkeypatch):
    page = FakeResponse(
        url="https://librivox.org/book/",
        text='<a href="https://archive.org/download/book/book.zip">ZIP</a>',
    )
    archive = FakeResponse(
        url="https://archive.org/download/book/book.zip",
        chunks=(b"abc",),
    )
    responses = iter((page, archive))
    monkeypatch.setattr(librivox_client.requests, "get", lambda *_args, **_kwargs: next(responses))
    progress: list[DownloadProgress] = []

    download_project_archive(
        "https://librivox.org/book/",
        tmp_path,
        progress_callback=progress.append,
    )

    assert progress == [DownloadProgress(0, None), DownloadProgress(3, None)]