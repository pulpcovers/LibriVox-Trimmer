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


def metadata_page(*readers):
    rows = []
    for index, reader in enumerate(readers, 1):
        credit = f'<a href="/reader/{reader}">Reader {reader}</a>' if reader else "Unknown"
        rows.append(
            f'<tr><td><a href="https://archive.org/book_{index}_64kb.mp3">Play</a> {index:02}</td>'
            f'<td><a class="chapter-name" href="https://archive.org/book_{index}_128kb.mp3">Chapter {index}</a></td>'
            f'<td>{credit}</td></tr>'
        )
    return (
        '<h1 class="logo">LibriVox</h1><h1>Example Book</h1>'
        '<p class="book-page-author"><a href="/author/1">Example Author<span>(1900 - 2000)</span></a></p>'
        '<p class="book-page-genre"><span>Language:</span> English</p>'
        '<table>' + ''.join(rows) + '</table>'
    )


@pytest.mark.parametrize("readers,narration,complete", [
    (("8463", "8463"), "single", True),
    (("8463", "16409"), "multiple", True),
    (("8463", None), "unknown", False),
    ((None, None), "unknown", False),
])
def test_metadata_classification_requires_complete_reader_credits(readers, narration, complete):
    result = librivox_client.parse_project_metadata(metadata_page(*readers), "https://librivox.org/book/")

    assert result["narration"] == narration
    assert result["reader_metadata_complete"] is complete
    assert result["title"] == "Example Book"
    assert result["authors"] == ["Example Author"]
    assert result["language"] == "English"
    assert len(result["chapters"]) == 2
    assert result["chapters"][0]["number"] == "01"
    assert result["chapters"][0]["audio_urls"] == [
        "https://archive.org/book_1_64kb.mp3", "https://archive.org/book_1_128kb.mp3",
    ]


def test_metadata_fetch_uses_only_project_page_and_closes_response(monkeypatch):
    page = FakeResponse(url="https://librivox.org/book/", text=metadata_page("8463"))
    calls = []
    monkeypatch.setattr(librivox_client.requests, "get", lambda url, **_kwargs: calls.append(url) or page)

    result = librivox_client.fetch_project_metadata(page.url)

    assert result["reader_count"] == 1
    assert calls == [page.url]
    assert page.closed


def test_metadata_missing_catalog_table_remains_unavailable():
    assert librivox_client.parse_project_metadata("<h1>Book</h1>", "https://librivox.org/book/") is None


def test_metadata_distinguishes_translators_from_authors():
    html = metadata_page("8463").replace(
        "</a></p>", '</a><br/>Translated by <a href="/author/2">Translator<span>(1900 - 2000)</span></a></p>', 1,
    )

    result = librivox_client.parse_project_metadata(html, "https://librivox.org/book/")

    assert result["authors"] == ["Example Author"]
    assert result["translators"] == ["Translator"]


@pytest.mark.parametrize("url", ["http://librivox.org/book/", "https://example.com/book/"])
def test_metadata_fetch_rejects_non_librivox_or_unencrypted_urls(url, monkeypatch):
    monkeypatch.setattr(librivox_client.requests, "get", lambda *_args, **_kwargs: pytest.fail("Unsafe fetch"))
    with pytest.raises(ValueError):
        librivox_client.fetch_project_metadata(url)


def test_download_reports_bytes_and_total_when_content_length_is_known(tmp_path, monkeypatch):
    page = FakeResponse(
        url="https://librivox.org/book/",
        text=metadata_page("8463") + '<a href="https://archive.org/download/book/book_128kb_mp3.zip">ZIP</a>',
    )
    archive = FakeResponse(
        url="https://archive.org/download/book/book_128kb_mp3.zip",
        headers={"content-length": "6"},
        chunks=(b"abc", b"def"),
    )
    responses = iter((page, archive))
    monkeypatch.setattr(librivox_client.requests, "get", lambda *_args, **_kwargs: next(responses))
    progress: list[DownloadProgress] = []
    metadata = []

    result = download_project_archive(
        "https://librivox.org/book/",
        tmp_path,
        progress_callback=progress.append,
        metadata_callback=metadata.append,
    )

    assert result.read_bytes() == b"abcdef"
    assert progress == [
        DownloadProgress(0, 6),
        DownloadProgress(3, 6),
        DownloadProgress(6, 6),
    ]
    assert archive.closed
    assert len(metadata) == 1
    assert metadata[0]["narration"] == "single"
    assert metadata[0]["chapters"][0]["readers"] == [{"id": "8463", "name": "Reader 8463"}]


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