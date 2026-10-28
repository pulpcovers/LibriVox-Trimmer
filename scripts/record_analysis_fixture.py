"""Record a track's audio analysis as a replay fixture for the detection tests.

The fixture stores the transcripts, speech regions and silences the analysis used, plus the cuts it chose.
The replay tests re-run the detection logic on that recording without audio or Whisper, so a logic change
that alters a recorded decision is caught immediately.

Examples (run from the repository root, with the app's Python environment):
    python scripts/record_analysis_fixture.py --project the-prisoner-of-zenda-by-anthony-hope --file zenda_03
    python scripts/record_analysis_fixture.py --project red-nails-by-robert-e-howard --file rednails_5
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from mutagen.mp3 import MP3  # noqa: E402

from librivox_trimmer.analysis_cache import AnalysisCache, use_cache  # noqa: E402
from librivox_trimmer.config import projects_dir  # noqa: E402
from librivox_trimmer.detection import analyze_track_boundaries  # noqa: E402
from librivox_trimmer.metadata import read_track  # noqa: E402
from librivox_trimmer.settings import DetectionSettings  # noqa: E402
from replay_support import summarize  # noqa: E402


def natural_key(path: Path):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.stem)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Project name without the .lvproj suffix")
    parser.add_argument("--file", required=True, help="Part of the chapter file name, e.g. zenda_03")
    parser.add_argument("--projects-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tests" / "fixtures" / "analysis")
    args = parser.parse_args()

    folder = (args.projects_dir or projects_dir()) / f"{args.project}_files"
    tracks = sorted(folder.rglob("*.mp3"), key=natural_key)
    matches = [track for track in tracks if args.file in track.stem]
    if len(matches) != 1:
        sys.exit(f"Expected one chapter matching {args.file!r}, found {len(matches)} in {folder}")
    track = matches[0]
    position = tracks.index(track)
    is_first, is_last = position == 0, position == len(tracks) - 1

    settings = DetectionSettings()
    cache = AnalysisCache(model_size=settings.model_size)
    with use_cache(cache):
        result = analyze_track_boundaries(
            track, settings=settings, is_first_chapter=is_first, is_last_chapter=is_last,
        )
    duration, title, _artist = read_track(track)
    tags = MP3(track).tags
    fixture = {
        "source": track.stem,
        "title": title,
        "duration": duration,
        "album": str(tags.get("TALB", "")) if tags else "",
        "is_first": is_first,
        "is_last": is_last,
        "audio": cache.snapshot(),
        "expected": summarize(result),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{track.stem}.json"
    out_path.write_text(json.dumps(fixture, separators=(",", ":")), encoding="utf-8")
    print(f"Recorded {out_path} ({out_path.stat().st_size // 1024} KB): {fixture['expected']}")


if __name__ == "__main__":
    main()
