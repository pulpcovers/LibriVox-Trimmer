import portalocker

from librivox_trimmer.download_state import (
    cleanup_incomplete_downloads,
    is_managed_assets_dir,
    lock_download_directory,
    mark_managed_directory,
)


def test_cleanup_leaves_unrelated_folders_that_only_look_like_download_folders(tmp_path):
    projects_directory = tmp_path / "Projects"
    saved_page = projects_directory / "Some Page_files"
    saved_page.mkdir(parents=True)
    (saved_page / "style.css").write_text("body {}", encoding="utf-8")
    marked = projects_directory / "marked_files"
    marked.mkdir()
    (marked / "anything.bin").write_bytes(b"x")
    mark_managed_directory(marked)

    removed = cleanup_incomplete_downloads(projects_directory)

    assert removed == [marked]
    assert saved_page.exists()
    assert not is_managed_assets_dir(saved_page)


def test_cleanup_removes_orphaned_download_but_preserves_completed_project(tmp_path):
    projects_directory = tmp_path / "Projects"
    incomplete = projects_directory / "unfinished-book_files"
    incomplete.mkdir(parents=True)
    (incomplete / "chapters.zip").write_bytes(b"partial")
    complete = projects_directory / "finished-book_files"
    complete.mkdir()
    (projects_directory / "finished-book.lvproj").write_text("{}", encoding="utf-8")

    removed = cleanup_incomplete_downloads(projects_directory)

    assert removed == [incomplete]
    assert not incomplete.exists()
    assert complete.exists()


def test_an_analysis_cache_folder_of_json_files_keeps_an_older_download_folder_recognised(tmp_path):
    assets = tmp_path / "book_files"
    (assets / "chapters").mkdir(parents=True)
    (assets / "chapters" / "01.mp3").write_bytes(b"mp3")
    cache = assets / "analysis-cache"
    cache.mkdir()
    (cache / "abc-base.en.json").write_text("{}", encoding="utf-8")

    assert is_managed_assets_dir(assets)

    (cache / "notes.docx").write_bytes(b"someone's file")

    assert not is_managed_assets_dir(assets)


def test_cleanup_skips_a_download_locked_by_another_process(tmp_path):
    projects_directory = tmp_path / "Projects"
    assets_directory = projects_directory / "active-book_files"
    assets_directory.mkdir(parents=True)
    (assets_directory / "chapters.zip").write_bytes(b"in progress")

    with lock_download_directory(projects_directory, assets_directory):
        removed = cleanup_incomplete_downloads(projects_directory)
        assert removed == []
        assert assets_directory.exists()

    removed = cleanup_incomplete_downloads(projects_directory)

    assert removed == [assets_directory]
    assert not assets_directory.exists()