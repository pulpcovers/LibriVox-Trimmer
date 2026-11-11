"""Compare intro retry control flow with shared benchmark-only ASR observations."""

import argparse
import ast
from dataclasses import asdict
from hashlib import sha256
import inspect
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from librivox_trimmer import detection
from librivox_trimmer.analysis_cache import AnalysisCache, _fingerprint, use_cache
from librivox_trimmer.model_manager import load_whisper_model
from librivox_trimmer.project import load_project
from librivox_trimmer.settings import DetectionSettings
from librivox_trimmer.transcription import TimedWord


def decision(result):
    def rounded(value):
        return None if value is None else round(value, 3)

    return {
        "intro": None if result.intro is None else [
            rounded(result.intro.removal_start_seconds), rounded(result.intro.keep_time_seconds),
        ],
        "outro": None if result.outro is None else [
            rounded(result.outro.keep_time_seconds), rounded(result.outro.resume_after_outro_seconds),
        ],
        "intro_review": bool(result.intro_review_reason),
        "outro_review": bool(result.outro_review_reason),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    baseline_source = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", args.baseline_ref + ":src/librivox_trimmer/detection.py"], text=True,
    )
    node = next(
        item for item in ast.parse(baseline_source).body
        if isinstance(item, ast.FunctionDef) and item.name == "_transcribe_intro"
    )
    namespace = dict(vars(detection))
    exec(compile(ast.Module(body=[node], type_ignores=[]), "baseline_intro", "exec"), namespace)
    baseline = namespace["_transcribe_intro"]
    candidate = detection._transcribe_intro
    cases = json.loads((args.recordings / "cases.json").read_text(encoding="utf-8"))
    if args.limit is not None:
        cases = cases[:args.limit]
    recorded = {}
    for mode in ("off", "on"):
        for line in (args.recordings / f"baseline-{mode}.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            recorded.setdefault(row["case"], {}).update({
                (tuple(item["window"]), item.get("hotwords")): item
                for item in row["observations"]
            })
    observations_path = args.output.with_suffix(".observations.json")
    if observations_path.exists():
        for item in json.loads(observations_path.read_text(encoding="utf-8")):
            recorded.setdefault(item["case"], {})[(tuple(item["window"]), item.get("hotwords"))] = item["observation"]
    original_extract = detection.extract_analysis_slice
    original_transcribe = detection.transcribe_words
    model = None
    rows = []
    measurements = {}
    current = {}
    windows = {}
    calls = []

    def extract(source, output, start, end):
        result = original_extract(source, output, start, end)
        windows[str(result)] = (round(start, 3), round(end, 3))
        return result

    def transcribe(path, **options):
        nonlocal model
        key = (windows[str(path)], options.get("hotwords"))
        observation = current["observations"].get(key)
        if observation is None:
            if model is None:
                model = load_whisper_model("base.en")
            options["model"] = model
            started = time.perf_counter()
            words = original_transcribe(path, **options)
            observation = {"words": [asdict(word) for word in words], "seconds": time.perf_counter() - started}
            current["observations"][key] = observation
        calls.append({"slice": Path(path).stem, "window": key[0], "hotwords": key[1], "seconds": observation["seconds"]})
        return [TimedWord(**word) for word in observation["words"]]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    detection.extract_analysis_slice = extract
    detection.transcribe_words = transcribe
    try:
        for case in cases:
            project_path = Path(case["project"])
            project_hash = sha256(project_path.read_bytes()).hexdigest()
            project = load_project(project_path)
            current["observations"] = recorded.setdefault(case["id"], {})
            audio_fingerprint = _fingerprint(Path(case["file"]))
            if any(
                observation.get("audio", audio_fingerprint) != audio_fingerprint
                for observation in current["observations"].values()
            ):
                raise RuntimeError("Recorded observations belong to different audio: " + case["id"])
            for hints in (False, True):
                for label, function in (("baseline", baseline), ("optimized", candidate)):
                    calls.clear()
                    windows.clear()
                    detection._transcribe_intro = function
                    settings = DetectionSettings(narrator_credit_hotwords=hints)
                    snapshot = measurements.get(case["id"], {})
                    snapshot = {key: value for key, value in snapshot.items() if key != "transcripts"}
                    cache = AnalysisCache.from_snapshot(snapshot, model_size="base.en")
                    started = time.perf_counter()
                    with use_cache(cache):
                        result = detection.analyze_track_boundaries(
                            Path(case["file"]), model=object(), settings=settings,
                            book_metadata=project.book_metadata, is_first_chapter=case["is_first"],
                            is_last_chapter=case["is_last"],
                        )
                    measurements[case["id"]] = cache.snapshot()
                    rows.append({
                        "case": case["id"], "variant": label, "hints": hints,
                        "decision": decision(result), "result": asdict(result), "calls": list(calls),
                        "asr_seconds": sum(call["seconds"] for call in calls),
                        "wall_seconds": time.perf_counter() - started,
                    })
                    print(f"{case['id']} hints={hints} {label}: {len(calls)} ASR calls", flush=True)
                    if sha256(project_path.read_bytes()).hexdigest() != project_hash:
                        raise RuntimeError("Project modified: " + case["id"])
    finally:
        detection._transcribe_intro = candidate
        detection.extract_analysis_slice = original_extract
        detection.transcribe_words = original_transcribe
    indexed = {(row["case"], row["hints"], row["variant"]): row for row in rows}
    comparisons = []
    for case in cases:
        for hints in (False, True):
            before = indexed[(case["id"], hints, "baseline")]
            after = indexed[(case["id"], hints, "optimized")]
            comparisons.append({
                "case": case["id"], "hints": hints,
                "calls_saved": len(before["calls"]) - len(after["calls"]),
                "recorded_asr_seconds_saved": before["asr_seconds"] - after["asr_seconds"],
                "decision_changed": before["decision"] != after["decision"],
                "before": before["decision"], "after": after["decision"],
                "warning_text_changed": any(
                    before["result"][key] != after["result"][key]
                    for key in ("intro_review_reason", "outro_review_reason")
                ),
            })
    output = {
        "baseline_ref": args.baseline_ref, "chapters": len(cases),
        "books": len({case["project"] for case in cases}),
        "baseline_intro_sha256": sha256(ast.unparse(node).encode()).hexdigest(),
        "candidate_intro_sha256": sha256(inspect.getsource(candidate).encode()).hexdigest(),
        "baseline_calls": sum(len(row["calls"]) for row in rows if row["variant"] == "baseline"),
        "optimized_calls": sum(len(row["calls"]) for row in rows if row["variant"] == "optimized"),
        "comparisons": comparisons, "runs": rows,
        "limitations": "Controlled ASR-observation comparison, not a fresh-run latency or labelled accuracy benchmark. Both variants use current normalization and metadata, no project or application cache writes. Missing ASR observations are collected once and shared.",
    }
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    observations_path.write_text(json.dumps([
        {"case": case, "window": key[0], "hotwords": key[1], "observation": observation}
        for case, observations in recorded.items() for key, observation in observations.items()
    ]), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key not in {"runs", "comparisons"}}, indent=2))
    print("Decision changes:", sum(item["decision_changed"] for item in comparisons))


if __name__ == "__main__":
    main()