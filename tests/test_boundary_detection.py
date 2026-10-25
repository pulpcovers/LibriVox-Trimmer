import pytest

from librivox_trimmer.boundary_detection import detect_boundaries
from librivox_trimmer.transcription import TimedWord


def make_words(text: str, start: float = 0) -> list[TimedWord]:
    return [
        TimedWord(word, start + index * 0.4, start + index * 0.4 + 0.25, 0.95)
        for index, word in enumerate(text.split())
    ]


def test_detects_disclaimer_end_and_outro_start():
    intro = make_words(
        "This is a LibriVox recording. All LibriVox recordings are in the public "
        "domain. For more information or to volunteer please visit LibriVox org."
    )
    outro = make_words("End of chapter one. Recording by Jane Reader.", start=540)

    result = detect_boundaries(intro, outro)

    assert result.intro is not None
    assert result.intro.keep_time_seconds - intro[-1].end >= 0.59
    assert result.intro.confidence > 90
    assert result.outro is not None
    assert abs(outro[0].start - result.outro.keep_time_seconds) < 0.001
    assert "end of chapter" in result.outro.matched_text


def test_returns_no_suggestions_for_unmatched_speech():
    ordinary_speech = make_words("The afternoon sun fell across the quiet valley.")

    result = detect_boundaries(ordinary_speech, ordinary_speech)

    assert result.intro is None
    assert result.outro is None


def test_preserves_chapter_announcement_before_notice():
    announcement = make_words("Chapter three An Angry King", start=0.5)
    notice = make_words(
        "This is a LibriVox recording All LibriVox recordings are in the public domain",
        start=5,
    )
    result = detect_boundaries(announcement + notice, [])
    assert result.intro is not None
    assert announcement[-1].end <= result.intro.removal_start_seconds <= notice[0].start
    assert result.intro.keep_time_seconds > notice[-1].end


def test_notice_before_chapter_heading_still_removes_only_leading_notice():
    notice = make_words("This is a LibriVox recording")
    heading = make_words("Chapter three An Angry King", start=5)
    result = detect_boundaries(notice + heading, [])
    assert result.intro is not None
    assert result.intro.removal_start_seconds == 0
    assert result.intro.keep_time_seconds < heading[0].start


def test_first_chapter_keeps_richer_repeated_announcement_after_notice():
    before = make_words("Part one Chapter one of The Mad King")
    notice = make_words("This is a LibriVox recording", start=6)
    after = make_words("The Mad King by Edgar Rice Burroughs Part 1 Chapter 1 A Runaway Horse", start=12)
    result = detect_boundaries(before + notice + after, [], is_first_chapter=True)
    assert result.intro is not None
    assert result.intro.removal_start_seconds == 0
    assert result.intro.keep_time_seconds < after[0].start
    ordinary = detect_boundaries(before + notice + after, [])
    assert ordinary.intro.removal_start_seconds == 0


def test_first_chapter_does_not_drop_unique_author_or_different_chapter():
    before = make_words("Chapter one The Mad King by Edgar Rice Burroughs")
    notice = make_words("This is a LibriVox recording", start=7)
    after = make_words("Chapter one The Mad King", start=12)
    result = detect_boundaries(before + notice + after, [], is_first_chapter=True)
    assert result.intro.removal_start_seconds > 0


def test_later_chapter_drops_book_information_and_earlier_repeated_number():
    before = make_words("Chapter 4 of Conan Beyond the Black River by Robert E Howard")
    notice = make_words("This is a LibriVox recording", start=10)
    after = make_words("Chapter IV The Beasts of Zogar Sag", start=15)
    result = detect_boundaries(before + notice + after, [])
    assert result.intro.removal_start_seconds == 0
    assert result.intro.keep_time_seconds < after[0].start


@pytest.mark.parametrize(("before_text", "after_text"), [
    (
        "Chapter three of The Hour of the Dragon by Robert E Howard",
        "The Hour of the Dragon III The Cliffs Reel",
    ),
    (
        "Chapter fourteen of The Hour of the Dragon by Robert E Howard",
        "The Hour of the Dragon 14 The Black Hand of Set",
    ),
    (
        "Chapter eighteen of The Hour of the Dragon by Robert E Howard",
        "The Hour of the Dragon Chapter fifteen I Am the Woman Who Never Died",
    ),
])
def test_later_chapter_drops_heading_when_title_repeats_but_number_does_not(
    before_text, after_text,
):
    before = make_words(before_text)
    notice = make_words("This is a LibriVox recording", start=8)
    after = make_words(after_text, start=14)

    result = detect_boundaries(before + notice + after, [])

    assert result.intro is not None
    assert result.intro.removal_start_seconds == 0
    assert result.intro.keep_time_seconds < after[0].start


def test_later_chapter_keeps_heading_when_only_author_name_repeats():
    before = make_words("Chapter three of The Hour of the Dragon by Robert E Howard")
    notice = make_words("This is a LibriVox recording", start=8)
    after = make_words("Robert Howard waited in the hall", start=14)

    result = detect_boundaries(before + notice + after, [])

    assert result.intro is not None
    assert result.intro.removal_start_seconds > 0


def test_singular_public_domain_notice_survives_misheard_librivox_name():
    heading = make_words("Chapter 2 of Conan Beyond the Black River by Robert E Howard")
    notice = make_words("This labor -fox recording is in the public domain", start=7)
    repeated = make_words("Chapter 2 The Wizard of Gwawela", start=13)
    result = detect_boundaries(heading + notice + repeated, [])
    assert result.intro is not None
    assert notice[-1].end < result.intro.keep_time_seconds < repeated[0].start
    assert result.intro.removal_start_seconds == 0


def test_later_chapter_proposes_cut_after_number_but_defaults_to_whole_heading():
    before = make_words("Part one Chapter three of The Mad King by Anthony Author")
    notice = make_words("This is a LibriVox recording", start=10)
    after = make_words("An Angry King", start=15)
    result = detect_boundaries(before + notice + after, [])
    assert result.intro.heading_cut_seconds == before[3].end
    assert before[-1].end < result.intro.removal_start_seconds < notice[0].start


def test_first_notice_retains_attached_reader_credit_and_following_title():
    notice = make_words("This is a LibriVox recording")
    credit = make_words("Recording by Phil Reader.", start=2.7)
    title = make_words("Beyond the Black River by Robert Howard", start=5.3)
    result = detect_boundaries(notice + credit + title, [], is_first_chapter=True)
    assert notice[-1].end < result.intro.keep_time_seconds < credit[0].start


def test_first_notice_retains_reading_by_credit():
    notice = make_words("This is a LibriVox recording")
    credit = make_words("Reading by Mark Nelson.", start=2.7)
    heading = make_words("Chapter one O Sleeper Awake", start=5.3)
    result = detect_boundaries(notice + credit + heading, [], is_first_chapter=True)
    assert notice[-1].end < result.intro.keep_time_seconds < credit[0].start


def test_first_track_retains_credit_before_notice_without_chapter_heading():
    opening = make_words("Introduction to the book. Read by Michelle Fry.")
    notice = make_words("This is a LibriVox recording.", start=5)
    narration = make_words("The story begins.", start=10)
    result = detect_boundaries(opening + notice + narration, [], is_first_chapter=True)
    assert opening[-1].end < result.intro.removal_start_seconds < notice[0].start
    assert result.intro.keep_time_seconds > notice[-1].end


def test_later_track_removes_credit_before_notice():
    opening = make_words("Introduction to the book. Read by Michelle Fry.")
    notice = make_words("This is a LibriVox recording.", start=5)
    result = detect_boundaries(opening + notice, [])
    assert result.intro.removal_start_seconds == 0
    assert result.intro.keep_time_seconds > notice[-1].end


def test_later_track_removes_attached_reader_credit():
    notice = make_words("This is a LibriVox recording.")
    credit = make_words("Reading by Mark Nelson.", start=2.7)
    heading = make_words("Chapter two.", start=6)
    result = detect_boundaries(notice + credit + heading, [])
    assert credit[-1].end < result.intro.keep_time_seconds < heading[0].start


def test_reader_credit_includes_location_after_short_pause():
    notice = make_words("This LibriVox recording is in the public domain.", start=8)
    credit = [
        TimedWord("Read", 12.1, 12.24), TimedWord("by", 12.24, 12.52),
        TimedWord("Michelle", 12.52, 12.82), TimedWord("Fry,", 12.82, 13.16),
        TimedWord("Baton", 13.54, 13.74), TimedWord("Rouge,", 13.74, 14.06),
        TimedWord("Louisiana.", 14.7, 14.72),
    ]
    heading = make_words("Chapter two The Pros and Cons", start=15.9)
    result = detect_boundaries(notice + credit + heading, [])
    assert credit[-1].end < result.intro.keep_time_seconds < heading[0].start


def test_reader_credit_after_longer_pause_stops_before_chapter_declaration():
    notice = make_words("This LibriVox recording is in the public domain.", start=7.65)
    credit = [
        TimedWord("Read", 12.34, 12.58), TimedWord("by", 12.58, 13),
        TimedWord("Michelle", 13, 13.38), TimedWord("Frye,", 13.38, 14.1),
        TimedWord("Baton", 14.46, 14.46), TimedWord("Rouge,", 14.46, 14.74),
        TimedWord("Louisiana", 14.86, 15.16),
    ]
    heading = make_words("Chapter four Ned Land", start=15.5)
    result = detect_boundaries(notice + credit + heading, [])
    assert abs(result.intro.keep_time_seconds - 15.76) < 0.001


def test_reader_credit_is_not_attached_across_intervening_narration():
    notice = make_words("This is a LibriVox recording.")
    story = make_words("He", start=2.1)
    credit = make_words("read by the window.", start=2.6)
    result = detect_boundaries(notice + story + credit, [])
    assert result.intro.keep_time_seconds < credit[0].start


def test_last_chapter_can_pair_numbered_marker_with_prefixed_book_title():
    first_pass = make_words("Chapter VIII In Dove Conan Beyon the Black River", start=100)
    title_pass = make_words("End of Conan Beyond the Black River by Robert E Howard", start=101.5)
    result = detect_boundaries(
        [], first_pass, is_last_chapter=True, book_title="Beyond the Black River",
        outro_transcripts=[title_pass],
    )
    assert result.outro is not None
    assert result.outro.keep_time_seconds == first_pass[0].start
    assert result.outro.resume_after_outro_seconds == title_pass[0].start


def test_last_chapter_removes_chapter_ending_but_resumes_at_book_ending():
    closing = make_words("End of chapter twenty eight", start=100)
    book = make_words("End of The Mad King by Edgar Rice Burroughs", start=105)
    result = detect_boundaries([], closing + book, is_last_chapter=True, book_title="The Mad King")
    assert result.outro.keep_time_seconds == 100
    assert result.outro.resume_after_outro_seconds == 105


def test_last_chapter_without_separable_book_ending_is_left_for_review():
    words = make_words("End of The Mad King by Edgar Rice Burroughs", start=105)
    assert detect_boundaries([], words, is_last_chapter=True, book_title="The Mad King").outro is None


def test_last_chapter_accepts_spoken_numbers_beyond_ten():
    closing = make_words("Chapter Twelve", start=100)
    book = make_words("End of The Mad King", start=102)
    result = detect_boundaries([], closing + book, is_last_chapter=True, book_title="The Mad King")
    assert result.outro is not None
    assert result.outro.keep_time_seconds == 100


def test_section_endings_are_outro_markers():
    closing = make_words("into the ark. End of section 2", start=640)
    result = detect_boundaries([], closing)
    assert result.outro.keep_time_seconds == closing[3].start


@pytest.mark.parametrize("marker", [
    "End of Volume XX",
    "End of the Volume 2",
    "End of Book 2 Chapter 3",
    "End of the Book II Chapter three",
])
def test_numbered_book_and_volume_markers_are_outros_near_track_end(marker):
    closing = make_words(marker, start=92)
    result = detect_boundaries([], closing, duration_seconds=100)

    assert result.outro is not None
    assert result.outro.keep_time_seconds == closing[0].start
    assert result.outro.matched_text == " ".join(word.text.casefold() for word in closing)


@pytest.mark.parametrize("marker", ["End of Book 2 Chapter 3", "End of Volume XX"])
def test_numbered_book_and_volume_markers_are_ignored_away_from_track_end(marker):
    words = make_words(marker, start=80)

    assert detect_boundaries([], words, duration_seconds=100).outro is None


def test_generic_the_end_is_not_a_structured_outro_marker():
    words = make_words("The End", start=98)

    assert detect_boundaries([], words, duration_seconds=100).outro is None


def test_final_chapter_recognizes_numbered_metadata_without_album_title():
    words = make_words("End of Book 2 Chapter 3", start=92)

    result = detect_boundaries(
        [], words, is_last_chapter=True, duration_seconds=100,
    )

    assert result.outro is not None
    assert result.outro.matched_text == "end of book 2 chapter 3"


def test_final_chapter_without_album_title_does_not_broaden_generic_outro_matching():
    words = make_words("End of chapter", start=92)

    result = detect_boundaries([], words, is_last_chapter=True, duration_seconds=100)

    assert result.outro is None


def test_last_chapter_matches_section_marker_and_title_without_edition_note():
    closing = make_words("Amen. End of section 8.", start=335)
    book = make_words("End of the First Epistle of Clement to the Corinthians by Clement of Rome", start=338.3)
    result = detect_boundaries(
        [], closing + book, is_last_chapter=True,
        book_title="The First Epistle of Clement to the Corinthians (Version 2, 65 chapters)",
    )
    assert result.outro.keep_time_seconds == closing[1].start
    assert result.outro.resume_after_outro_seconds == book[0].start


def test_last_chapter_matches_digit_title_and_removes_whole_part_chapter_marker():
    closing = make_words("End of Part 2, Chapter 22.", start=225.7)
    ending = make_words("End of 20", start=229.7) + [TimedWord(",000", 230.9, 231.2, 0.95)]
    ending += make_words("leagues under the sea.", start=231.3)
    result = detect_boundaries(
        [], closing + ending, is_last_chapter=True,
        book_title="Twenty Thousand Leagues Under The Sea",
    )
    assert result.outro is not None
    assert result.outro.keep_time_seconds == closing[0].start
    assert result.outro.resume_after_outro_seconds == ending[0].start