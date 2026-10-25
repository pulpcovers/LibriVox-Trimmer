from pathlib import Path

from librivox_trimmer.models import natural_path_key


def test_natural_path_key_sorts_chapter_numbers_numerically():
    paths = [
        Path("10 - Chapter Ten.mp3"),
        Path("02 - Chapter Two.mp3"),
        Path("01 - Chapter One.mp3"),
    ]

    ordered = sorted(paths, key=natural_path_key)

    assert [path.name for path in ordered] == [
        "01 - Chapter One.mp3",
        "02 - Chapter Two.mp3",
        "10 - Chapter Ten.mp3",
    ]