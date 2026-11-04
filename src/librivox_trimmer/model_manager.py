from functools import lru_cache

from librivox_trimmer.config import model_cache_dir


DEFAULT_MODEL_SIZE = "base.en"


@lru_cache(maxsize=2)
def load_whisper_model(model_size: str = DEFAULT_MODEL_SIZE):
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "Speech recognition is unavailable. Reinstall the app and try again. "
            "If you run from source, activate your Python environment and run "
            "python -m pip install -e . from the source folder."
        ) from error

    cache_directory = model_cache_dir()
    cache_directory.mkdir(parents=True, exist_ok=True)
    return WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=str(cache_directory),
    )