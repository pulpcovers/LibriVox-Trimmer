"""Helpers shared by the replay tests and the fixture recording script."""


def summarize(result) -> dict:
    """The decisions a replay must reproduce, rounded so float noise cannot fail a test."""

    def rounded(value):
        return None if value is None else round(value, 2)

    intro, outro = result.intro, result.outro
    return {
        "intro": None if intro is None else {
            "keep": rounded(intro.keep_time_seconds),
            "removal_start": rounded(intro.removal_start_seconds),
        },
        "intro_review": bool(result.intro_review_reason),
        "outro": None if outro is None else {
            "keep": rounded(outro.keep_time_seconds),
            "resume": rounded(outro.resume_after_outro_seconds),
        },
        "outro_review": bool(result.outro_review_reason),
    }
