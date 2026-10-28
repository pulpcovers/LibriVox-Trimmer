from dataclasses import dataclass, replace
from statistics import mean
import re
from typing import Sequence

from rapidfuzz import fuzz

from librivox_trimmer.transcription import TimedWord


INTRO_PHRASES = (
    "this is a librivox recording",
    "this librivox recording is in the public domain",
    "all librivox recordings are in the public domain",
    "for more information or to volunteer please visit librivox org",
    "for further information and to find out how you could volunteer please go to librivox org",
)

OUTRO_PHRASES = (
    "end of chapter",
    "end of the chapter",
    "end of section",
    "end of the section",
    "end of introduction",
    "end of the introduction",
    "end of prologue",
    "end of the prologue",
    "end of the book",
    "read by",
    "recording by",
    "narrated by",
)

MATCH_THRESHOLD = 82.0
STRUCTURED_OUTRO_LOOKBACK_SECONDS = 10.0


@dataclass(frozen=True)
class PhraseMatch:
    phrase: str
    text: str
    start: float
    end: float
    score: float


@dataclass(frozen=True)
class BoundarySuggestion:
    keep_time_seconds: float
    confidence: float
    matched_text: str
    # Latest time an intro removal start may move to when snapped into a pause.
    removal_refinement_limit_seconds: float | None = None
    removal_start_seconds: float = 0.0
    resume_after_outro_seconds: float | None = None
    retained_ending_match_end_seconds: float | None = None
    notice_end_seconds: float | None = None
    # Cut after the spoken chapter number, used instead only if a clear pause follows it.
    heading_cut_seconds: float | None = None
    reader_credit_start_seconds: float | None = None
    reader_credit_end_seconds: float | None = None
    notice_only_end_seconds: float | None = None
    reader_credit_prefix_end_seconds: float | None = None
    # The outro was found by matching the chapter's opening announcement, so its first heard word may be late.
    announcement_outro: bool = False
    # Start of the spoken chapter heading that follows removed credit/title lines.
    heading_start_seconds: float | None = None


@dataclass(frozen=True)
class BoundaryDetection:
    intro: BoundarySuggestion | None
    outro: BoundarySuggestion | None
    intro_review_reason: str | None = None
    outro_review_reason: str | None = None


_NUMBER_VALUES = {
    word: value for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_NUMBER_VALUES.update({
    word: (index + 2) * 10 for index, word in enumerate(
        "twenty thirty forty fifty sixty seventy eighty ninety".split()
    )
})
_ORDINAL_VALUES = {
    word: index + 1 for index, word in enumerate(
        "first second third fourth fifth sixth seventh eighth ninth tenth".split()
    )
}
_NUMBER_SCALES = {"thousand": 1000, "million": 1000000}
_ROMAN_PATTERN = re.compile(r"m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _number_value(token: str) -> int | None:
    """Value of a single normalized token written as digits, a number word, an ordinal or a Roman numeral."""
    if token.isdigit():
        return int(token)
    if token in _NUMBER_VALUES:
        return _NUMBER_VALUES[token]
    if token in _ORDINAL_VALUES:
        return _ORDINAL_VALUES[token]
    if token == "hundred":
        return 100
    if token and _ROMAN_PATTERN.fullmatch(token):
        values = [_ROMAN_VALUES[character] for character in token]
        return sum(
            -value if index + 1 < len(values) and value < values[index + 1] else value
            for index, value in enumerate(values)
        )
    return None


def _normalize_word(word: str) -> str:
    return "".join(character for character in word.casefold() if character.isalnum())


def _tokenize(words: Sequence[TimedWord]) -> list[TimedWord]:
    tokens: list[TimedWord] = []
    for word in words:
        normalized = _normalize_word(word.text)
        if normalized:
            tokens.append(
                TimedWord(
                    text=normalized,
                    start=word.start,
                    end=word.end,
                    probability=word.probability,
                )
            )
    return tokens


def _find_phrase_matches(
    words: Sequence[TimedWord],
    phrases: Sequence[str],
    *,
    threshold: float = MATCH_THRESHOLD,
) -> list[PhraseMatch]:
    tokens = _tokenize(words)
    matches: list[PhraseMatch] = []
    for phrase in phrases:
        phrase_tokens = phrase.split()
        phrase_text = " ".join(phrase_tokens)
        best_match: PhraseMatch | None = None
        minimum_length = max(1, len(phrase_tokens) - 2)
        maximum_length = len(phrase_tokens) + 2
        for start_index in range(len(tokens)):
            for token_count in range(minimum_length, maximum_length + 1):
                end_index = start_index + token_count
                if end_index > len(tokens):
                    continue
                candidate_tokens = tokens[start_index:end_index]
                candidate_text = " ".join(token.text for token in candidate_tokens)
                phrase_score = fuzz.ratio(phrase_text, candidate_text)
                if phrase_score < threshold:
                    continue
                probabilities = [
                    token.probability
                    for token in candidate_tokens
                    if token.probability is not None
                ]
                score = phrase_score
                if probabilities:
                    score = phrase_score * 0.8 + mean(probabilities) * 100 * 0.2
                match = PhraseMatch(
                    phrase=phrase,
                    text=candidate_text,
                    start=candidate_tokens[0].start,
                    end=candidate_tokens[-1].end,
                    score=score,
                )
                if best_match is None or match.score > best_match.score:
                    best_match = match
        if best_match is not None:
            matches.append(best_match)
    return matches


def _front_matter(words: Sequence[TimedWord], notice_end: float) -> tuple[bool, float | None, float | None]:
    """Whether a capitalised spoken heading ("Chapter 2") follows the notice, and where credit/title speech before it ends."""
    after = [word for word in words if _normalize_word(word.text) and word.start >= notice_end - 0.05]
    for index, word in enumerate(after[:-1]):
        if index > 40 or word.start - notice_end > 14.0:
            break
        if _normalize_word(word.text) not in {"chapter", "section", "part"}:
            continue
        # Lower-case mentions inside narration ("in chapter three") are not headings.
        if not word.text.strip()[:1].isupper() or _number_value(_normalize_word(after[index + 1].text)) is None:
            continue
        # "...by <name>" marks a credit or book/author line; a bare title belongs to the heading.
        if index == 0 or not any(_normalize_word(item.text) == "by" for item in after[:index]):
            return True, None, None
        return True, after[index - 1].end, word.start
    return False, None, None


def front_matter_pending(words: Sequence[TimedWord], notice_end: float) -> bool:
    """Credit or title speech ("... by <name>") follows the notice but no chapter heading has been heard yet."""
    if _front_matter(words, notice_end)[0]:
        return False
    return any(
        _normalize_word(word.text) == "by" and notice_end - 0.05 <= word.start <= notice_end + 14.0
        for word in words
    )


def detect_intro_boundary(
    words: Sequence[TimedWord], *, safety_gap_seconds: float = 0.6,
    retain_reader_credit: bool = False,
    chapter_title: str = "",
) -> BoundarySuggestion | None:
    matches = _find_phrase_matches(words, INTRO_PHRASES)
    if not matches:
        return None

    matches.sort(key=lambda match: (match.start, match.end))
    clusters: list[list[PhraseMatch]] = []
    for match in matches:
        if not clusters or match.start - max(item.end for item in clusters[-1]) > 12:
            clusters.append([match])
        else:
            clusters[-1].append(match)
    cluster = max(
        clusters,
        key=lambda items: mean(item.score for item in items)
        + min(10, 4 * (len({item.phrase for item in items}) - 1)),
    )
    distinct_phrases = len({match.phrase for match in cluster})
    confidence = min(
        99.0,
        mean(match.score for match in cluster) + min(10, 4 * (distinct_phrases - 1)),
    )
    matched_text = "; ".join(match.text for match in cluster)
    notice_end = max(match.end for match in cluster)
    # A doubled "LibriVox" can make the best match stop before the spoken "org".
    following_token = next((word for word in _tokenize(words) if word.start >= notice_end - 0.05), None)
    if (
        following_token is not None
        and following_token.text == "org"
        and following_token.start - notice_end <= 0.5
    ):
        notice_end = following_token.end
    notice_only_end = notice_end
    retained_credit_start = None
    reader_credit_start = None
    reader_credit_end = None
    reader_credit_prefix_end = None
    title_text = re.sub(r"^\s*\d+\s*[-.:]\s*", "", chapter_title)
    title_text = " ".join(_normalize_word(word) for word in title_text.split())
    headings = (
        _find_phrase_matches(words, (title_text,), threshold=88)
        if len(title_text.split()) >= 2 else []
    )
    credits = _find_phrase_matches(words, ("recording by", "reading by", "read by"), threshold=90)
    for credit in credits:
        if not notice_end - 0.05 <= credit.start <= notice_end + 2.0:
            continue
        if any(
            word.start >= notice_end and word.end <= credit.start
            and _normalize_word(word.text)
            for word in words
        ):
            continue
        if retain_reader_credit:
            retained_credit_start = credit.start
            break
        reader_credit_start = credit.start
        reader_credit_prefix_end = credit.end
        credit_end = credit.end
        confirmed_end = False
        heading_start = min(
            (heading.start for heading in headings if credit.end <= heading.start <= credit.end + 6),
            default=float("inf"),
        )
        for word in words:
            if word.end <= credit_end:
                continue
            if word.start >= heading_start or _normalize_word(word.text) in {"chapter", "section", "part"}:
                confirmed_end = credit_end > credit.end
                break
            if (
                word.start - credit_end > 1.0
                or word.end > credit.end + 6
            ):
                break
            credit_end = word.end
            if word.text.rstrip().endswith((".", "!", "?")):
                confirmed_end = True
                break
        if confirmed_end:
            reader_credit_end = credit_end
            notice_end = max(notice_end, credit_end)
        break
    heading_start = None
    if not retain_reader_credit:
        # Whatever the reader says between the notice and the chapter heading (credit, book title) is front matter.
        _found, front_matter_end, front_matter_heading = _front_matter(words, notice_end)
        if front_matter_end is not None and front_matter_end > notice_end:
            notice_end = front_matter_end
            heading_start = front_matter_heading
            reader_credit_start = reader_credit_end = reader_credit_prefix_end = None
    notice_start = min(match.start for match in cluster)
    preceding_words = [word for word in _tokenize(words) if word.end <= notice_start]
    has_chapter_announcement = any(
        word.text in {"chapter", "section", "part"} and _number_value(following.text) is not None
        for word, following in zip(preceding_words, preceding_words[1:])
    )
    removal_start = 0.0
    has_retained_credit = retain_reader_credit and any(credit.end <= notice_start for credit in credits)
    if (has_chapter_announcement or has_retained_credit or retain_reader_credit) and preceding_words:
        previous_end = preceding_words[-1].end
        removal_start = previous_end + max(0.0, notice_start - previous_end) / 2
    keep_time = max(0.0, notice_end + safety_gap_seconds)
    if retained_credit_start is not None:
        keep_time = min(keep_time, (notice_end + retained_credit_start) / 2)
    if reader_credit_start is not None and reader_credit_end is None:
        keep_time = min(notice_end, reader_credit_start)
    return BoundarySuggestion(
        keep_time_seconds=keep_time,
        confidence=confidence,
        matched_text=matched_text,
        removal_refinement_limit_seconds=min(cluster, key=lambda match: match.start).end,
        removal_start_seconds=removal_start,
        notice_end_seconds=notice_end,
        reader_credit_start_seconds=reader_credit_start,
        reader_credit_end_seconds=reader_credit_end,
        notice_only_end_seconds=notice_only_end,
        reader_credit_prefix_end_seconds=reader_credit_prefix_end,
        heading_start_seconds=heading_start,
    )


def _structured_numbered_outro_matches(
    words: Sequence[TimedWord], duration_seconds: float | None,
) -> list[PhraseMatch]:
    if duration_seconds is None:
        return []
    tokens = _tokenize(words)
    matches = []
    for index, token in enumerate(tokens):
        if token.text != "end" or index + 3 >= len(tokens) or tokens[index + 1].text != "of":
            continue
        label_index = index + 2
        if tokens[label_index].text == "the":
            label_index += 1
        label = tokens[label_index].text
        if label not in {"book", "volume", "part"} or label_index + 1 >= len(tokens):
            continue
        if tokens[index].start < duration_seconds - STRUCTURED_OUTRO_LOOKBACK_SECONDS:
            continue
        if _number_value(tokens[label_index + 1].text) is None:
            continue
        end_index = label_index + 1
        if (
            end_index + 2 < len(tokens)
            and tokens[end_index + 1].text == "chapter"
            and _number_value(tokens[end_index + 2].text) is not None
        ):
            end_index += 2
        matched = tokens[index:end_index + 1]
        matches.append(PhraseMatch(
            phrase=f"end of {label}",
            text=" ".join(word.text for word in matched),
            start=matched[0].start,
            end=matched[-1].end,
            score=96.0,
        ))
    return matches


def _suggest_structured_numbered_outro(
    words: Sequence[TimedWord], duration_seconds: float | None,
) -> BoundarySuggestion | None:
    matches = _structured_numbered_outro_matches(words, duration_seconds)
    if not matches:
        return None
    match = min(matches, key=lambda item: item.start)
    return BoundarySuggestion(
        keep_time_seconds=match.start,
        confidence=match.score,
        matched_text=match.text,
    )


def detect_outro_boundary(
    words: Sequence[TimedWord], *, safety_gap_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> BoundarySuggestion | None:
    matches = _find_phrase_matches(words, OUTRO_PHRASES, threshold=88.0)
    matches.extend(_structured_numbered_outro_matches(words, duration_seconds))
    if not matches:
        return None
    first_match = min(matches, key=lambda match: (match.start, -match.end))
    confidence = min(99.0, first_match.score)
    return BoundarySuggestion(
        keep_time_seconds=max(0.0, first_match.start - safety_gap_seconds),
        confidence=confidence,
        matched_text=first_match.text,
    )


def _announcement_parts(text):
    identities = {}
    content = []
    index = 0
    while index < len(text):
        token = text[index]
        if token in {"part", "chapter", "section"} and index + 1 < len(text):
            value = _number_value(text[index + 1])
            if value is not None:
                identities[token] = value
                index += 2
                continue
        if token not in {"of", "the", "by", "a", "an"}:
            content.append(token)
        index += 1
    return identities, content


def remove_duplicate_first_announcement(
    words: Sequence[TimedWord], intro: BoundarySuggestion, *, retain_book_information: bool = True,
) -> BoundarySuggestion:
    if intro.removal_start_seconds <= 0:
        return intro
    tokens = _tokenize(words)
    before = [word.text for word in tokens if word.end <= intro.removal_start_seconds]
    after = [
        word.text for word in tokens
        if word.end > intro.keep_time_seconds
        and word.start < intro.keep_time_seconds + 20
    ]
    announcement_parts = _announcement_parts

    earlier_ids, earlier_content = announcement_parts(before)
    later_ids, later_content = announcement_parts(after)
    repeated_announcement = bool(
        earlier_ids
        and all(later_ids.get(key) == value for key, value in earlier_ids.items())
        and len(earlier_content) >= 2
        and all(any(fuzz.ratio(token, other) >= 85 for other in later_content) for token in earlier_content)
    )
    if not retain_book_information:
        chapter_id = earlier_ids.get("chapter", earlier_ids.get("section"))
        later_id = later_ids.get("chapter", later_ids.get("section"))
        repeated_number = chapter_id is not None and chapter_id == later_id
        declaration = next((
            index for index, token in enumerate(before[:-1])
            if token in {"chapter", "section"} and _number_value(before[index + 1]) is not None
        ), None)
        title_start = declaration + 2 if declaration is not None else len(before)
        if title_start < len(before) and before[title_start] == "of":
            title_start += 1
        title_end = before.index("by", title_start) if "by" in before[title_start:] else len(before)
        title_content = [
            token for token in before[title_start:title_end]
            if token not in {"of", "the", "a", "an"}
        ]
        repeated_title = any(
            all(fuzz.ratio(title_content[start + offset], later_content[later + offset]) >= 85
                for offset in range(2))
            for start in range(max(0, len(title_content) - 1))
            for later in range(min(10, max(0, len(later_content) - 1)))
        )
        if repeated_number or repeated_title or repeated_announcement:
            return replace(intro, removal_start_seconds=0.0)
        before_words = [word for word in tokens if word.end <= intro.removal_start_seconds]
        declarations = [
            index for index, word in enumerate(before_words[:-1])
            if word.text in {"chapter", "section"}
            and _number_value(before_words[index + 1].text) is not None
        ]
        if declarations:
            end_index = declarations[-1] + 1
            if end_index + 1 >= len(before_words) or before_words[end_index + 1].text != "of":
                return intro
            chapter_end = before_words[end_index].end
            return replace(intro, heading_cut_seconds=chapter_end)
        return intro
    if repeated_announcement:
        return replace(intro, removal_start_seconds=0.0)
    return intro


def _title_variants(normalized_title: str) -> list[str]:
    # Whisper may write "Twenty Thousand" as "20,000", so also try the digit form.
    tokens, converted, index = normalized_title.split(), [], 0
    while index < len(tokens):
        if tokens[index] not in _NUMBER_VALUES:
            converted.append(tokens[index])
            index += 1
            continue
        total = current = 0
        while index < len(tokens) and (
            tokens[index] in _NUMBER_VALUES or tokens[index] == "hundred" or tokens[index] in _NUMBER_SCALES
        ):
            word = tokens[index]
            if word == "hundred":
                current *= 100
            elif word in _NUMBER_SCALES:
                total += current * _NUMBER_SCALES[word]
                current = 0
            else:
                current += _NUMBER_VALUES[word]
            index += 1
        converted.append(str(total + current))
    digit_title = " ".join(converted)
    return [normalized_title] if digit_title == normalized_title else [normalized_title, digit_title]


def _merge_digit_groups(words: Sequence[TimedWord]) -> list[TimedWord]:
    # Whisper splits "20,000" into "20" and ",000"; rejoin them so the number matches as one token.
    merged: list[TimedWord] = []
    for word in words:
        digits = _normalize_word(word.text)
        if (
            merged and len(digits) == 3 and digits.isdigit() and word.text.strip().startswith(",")
            and _normalize_word(merged[-1].text).isdigit()
        ):
            previous = merged[-1]
            merged[-1] = TimedWord(
                _normalize_word(previous.text) + digits, previous.start, word.end, previous.probability,
            )
        else:
            merged.append(word)
    return merged


def detect_final_chapter_outro(
    words: Sequence[TimedWord], book_title: str,
    *, other_transcripts: Sequence[Sequence[TimedWord]] = (),
    duration_seconds: float | None = None,
) -> BoundarySuggestion | None:
    # Album tags can carry edition notes such as "(Version 2, 65 chapters)" that are never spoken.
    spoken_title = re.sub(r"\([^)]*\)", " ", book_title)
    normalized_title = " ".join(re.findall(r"[a-z0-9]+", spoken_title.casefold()))
    if not normalized_title:
        return _suggest_structured_numbered_outro(words, duration_seconds)
    title_variants = _title_variants(normalized_title)
    transcripts = [_merge_digit_groups(transcript) for transcript in (words, *other_transcripts)]
    book_matches = []
    all_chapter_matches = []
    for transcript in transcripts:
        for title_text in title_variants:
            book_matches.extend(_find_phrase_matches(
                transcript, (f"end of {title_text}", f"end of the {title_text}"), threshold=88,
            ))
        titles = _find_phrase_matches(transcript, title_variants, threshold=88)
        endings = _find_phrase_matches(transcript, ("end of",), threshold=95)
        tokens = _tokenize(transcript)
        for title in titles:
            for ending in endings:
                between = [token.text for token in tokens if ending.end <= token.start < title.start]
                if (
                    ending.end <= title.start <= ending.end + 2
                    and len(between) <= 3 and "chapter" not in between and "section" not in between
                ):
                    book_matches.append(PhraseMatch(
                        "end of book", ending.text + " " + title.text,
                        ending.start, title.end, min(ending.score, title.score),
                    ))
        all_chapter_matches.extend(_find_phrase_matches(
            transcript,
            ("end of chapter", "end of the chapter", "end of section", "end of the section",
             "end of introduction", "end of the introduction", "end of prologue",
             "end of the prologue"),
            threshold=88,
        ))
        for position, (current, following) in enumerate(zip(tokens, tokens[1:])):
            number = following.text
            if current.text in {"chapter", "section"} and _number_value(number) is not None:
                start = current.start
                # Include a preceding "End of" or "End of Part 2," so it is not left before the cut.
                for back in range(position - 2, max(-1, position - 6), -1):
                    between = [token.text for token in tokens[back + 2:position]]
                    if (
                        tokens[back].text == "end" and tokens[back + 1].text == "of"
                        and current.start - tokens[back].start <= 4
                        and all(
                            text in {"part", "book", "volume", "section"} or _number_value(text) is not None
                            for text in between
                        )
                    ):
                        start = tokens[back].start
                        break
                all_chapter_matches.append(PhraseMatch(
                    "numbered chapter", f"{current.text} {number}", start, following.end, 90,
                ))
    if not book_matches:
        return _suggest_structured_numbered_outro(words, duration_seconds)
    book_end = min(book_matches, key=lambda match: match.start)
    chapter_matches = [match for match in all_chapter_matches
                       if match.end <= book_end.start and book_end.start - match.end <= 6]
    if not chapter_matches:
        return None
    chapter_end = min(chapter_matches, key=lambda match: match.start)
    return BoundarySuggestion(
        keep_time_seconds=chapter_end.start,
        confidence=min(99.0, chapter_end.score, book_end.score),
        matched_text=chapter_end.text,
        resume_after_outro_seconds=book_end.start,
        retained_ending_match_end_seconds=book_end.end,
    )


_LEAD_IN_WORDS = {"end", "and", "of"}


def _content_span_end(tokens: Sequence[TimedWord], first: int, content: Sequence[str]) -> TimedWord | None:
    """Last token of the span after `first` that contains every wanted word, or None."""
    span = tokens[first:first + len(content) + 6]
    used: set[int] = set()
    last = -1
    for wanted in content:
        for position, token in enumerate(span):
            if position not in used and fuzz.ratio(wanted, token.text) >= 85:
                used.add(position)
                last = max(last, position)
                break
        else:
            return None
    if span[last].end - span[0].start > 14:
        return None
    return span[last]


def _extend_over_lead_in(tokens: Sequence[TimedWord], index: int) -> int:
    # Whisper often hears "End of" as "And of" or drops it, so include what is left of it.
    while (
        index > 0 and tokens[index - 1].text in _LEAD_IN_WORDS
        and tokens[index].start - tokens[index - 1].end <= 1.5
    ):
        index -= 1
    return index


def detect_repeated_announcement_outro(
    intro_words: Sequence[TimedWord],
    transcripts: Sequence[Sequence[TimedWord]],
    duration_seconds: float | None,
    *, with_book_ending: bool = False,
) -> BoundarySuggestion | None:
    """Find a closing announcement ("End of Part 3 of Red Nails by ...") by matching the opening one."""
    if duration_seconds is None:
        return None
    notice = _find_phrase_matches(intro_words, INTRO_PHRASES)
    limit = min((match.start for match in notice), default=10.0)
    identities, content = _announcement_parts(
        [word.text for word in _tokenize(intro_words) if word.end <= limit]
    )
    if not identities or len(content) < 2:
        return None
    wanted_numbers = set(identities.values())
    prepared = [
        [token for token in _tokenize(_merge_digit_groups(transcript))
         if token.start >= duration_seconds - 30.0]
        for transcript in transcripts
    ]

    markers: list[PhraseMatch] = []
    for tokens in prepared:
        for index, token in enumerate(tokens):
            labelled = (
                token.text in identities and index + 1 < len(tokens)
                and _number_value(tokens[index + 1].text) == identities[token.text]
            )
            bare = _number_value(token.text) in wanted_numbers and (
                index == 0 or tokens[index - 1].text not in identities
            )
            if not (labelled or bare):
                continue
            last = _content_span_end(tokens, index + (2 if labelled else 1), content)
            if last is None:
                continue
            begin = _extend_over_lead_in(tokens, index)
            markers.append(PhraseMatch(
                "repeated announcement",
                " ".join(item.text for item in tokens if tokens[begin].start <= item.start <= last.start),
                tokens[begin].start, last.end, 92.0,
            ))
            break
    if not markers:
        return None
    marker = min(markers, key=lambda match: match.start)

    resume = retained_end = None
    if with_book_ending:
        endings: list[PhraseMatch] = []
        for tokens in prepared:
            for index, token in enumerate(tokens):
                if token.start < marker.end - 0.2:
                    continue
                if token.text not in _LEAD_IN_WORDS and not any(fuzz.ratio(word, token.text) >= 85 for word in content):
                    continue
                last = _content_span_end(tokens, index, content)
                if last is None:
                    continue
                begin = _extend_over_lead_in(tokens, index)
                endings.append(PhraseMatch(
                    "end of book", "", max(marker.end, tokens[begin].start), last.end, 90.0,
                ))
                break
        if endings:
            ending = min(endings, key=lambda match: match.start)
            resume, retained_end = ending.start, ending.end
    return BoundarySuggestion(
        keep_time_seconds=marker.start,
        confidence=marker.score,
        matched_text=marker.text,
        resume_after_outro_seconds=resume,
        retained_ending_match_end_seconds=retained_end,
        announcement_outro=True,
    )


def detect_boundaries(
    intro_words: Sequence[TimedWord],
    outro_words: Sequence[TimedWord],
    *,
    intro_safety_gap_seconds: float = 0.6,
    outro_safety_gap_seconds: float = 0.0,
    is_first_chapter: bool = False,
    is_last_chapter: bool = False,
    book_title: str = "",
    outro_transcripts: Sequence[Sequence[TimedWord]] = (),
    duration_seconds: float | None = None,
    chapter_title: str = "",
) -> BoundaryDetection:
    intro = detect_intro_boundary(
        intro_words,
        safety_gap_seconds=intro_safety_gap_seconds,
        retain_reader_credit=is_first_chapter,
        chapter_title=chapter_title,
    )
    if intro is not None:
        intro = remove_duplicate_first_announcement(
            intro_words, intro, retain_book_information=is_first_chapter,
        )
    if is_last_chapter:
        outro = detect_final_chapter_outro(
            outro_words, book_title, other_transcripts=outro_transcripts,
            duration_seconds=duration_seconds,
        )
    else:
        outro = detect_outro_boundary(
            outro_words, safety_gap_seconds=outro_safety_gap_seconds,
            duration_seconds=duration_seconds,
        )
    if outro is None:
        outro = detect_repeated_announcement_outro(
            intro_words, [outro_words, *outro_transcripts], duration_seconds,
            with_book_ending=is_last_chapter,
        )
    return BoundaryDetection(intro=intro, outro=outro)