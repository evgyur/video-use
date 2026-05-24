"""Transcribe a video with Groq Whisper or ElevenLabs Scribe.

Extracts mono 16kHz audio via ffmpeg, uploads to the configured backend
with word-level timestamps, writes a normalized response to
<edit_dir>/transcripts/<video_stem>.json.

Cached: if the output file already exists, the upload is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"


def load_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def load_transcription_config() -> tuple[str, str, str | None]:
    env_file = load_env_values()
    backend = (
        os.environ.get("TRANSCRIPTION_BACKEND")
        or os.environ.get("VIDEO_USE_TRANSCRIBE_BACKEND")
        or env_file.get("TRANSCRIPTION_BACKEND")
        or env_file.get("VIDEO_USE_TRANSCRIBE_BACKEND")
        or ""
    ).strip().lower()

    groq_key = env_file.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY", "")
    elevenlabs_key = env_file.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY", "")

    if not backend:
        backend = "groq" if groq_key else "elevenlabs"

    if backend == "groq":
        if not groq_key:
            sys.exit("GROQ_API_KEY not found in .env or environment")
        model = (
            os.environ.get("GROQ_TRANSCRIPTION_MODEL")
            or env_file.get("GROQ_TRANSCRIPTION_MODEL")
            or DEFAULT_GROQ_MODEL
        )
        return backend, groq_key, model

    if backend in {"elevenlabs", "scribe"}:
        if not elevenlabs_key:
            sys.exit("ELEVENLABS_API_KEY not found in .env or environment")
        return "elevenlabs", elevenlabs_key, None

    sys.exit(f"unsupported TRANSCRIPTION_BACKEND: {backend}")


def load_api_key() -> str:
    """Backward-compatible key loader for older callers."""
    _backend, api_key, _model = load_transcription_config()
    return api_key


def normalize_groq_payload(payload: dict, model: str) -> dict:
    words = []
    for w in payload.get("words", []) or []:
        text = (w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        words.append({
            "type": "word",
            "text": text,
            "start": w.get("start"),
            "end": w.get("end"),
        })
    return {
        "provider": "groq",
        "model": model,
        "text": payload.get("text", ""),
        "words": words,
        "segments": payload.get("segments", []),
        "raw": payload,
    }


def call_groq(
    audio_path: Path,
    api_key: str,
    model: str,
    language: str | None = None,
) -> dict:
    data: list[tuple[str, str]] = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("temperature", "0"),
        ("timestamp_granularities[]", "word"),
        ("timestamp_granularities[]", "segment"),
    ]
    if language:
        data.append(("language", language))

    with open(audio_path, "rb") as f:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Groq returned {resp.status_code}: {resp.text[:500]}")

    return normalize_groq_payload(resp.json(), model)


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
    backend: str = "elevenlabs",
    model: str | None = None,
    verbose: bool = True,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB) to {backend}", flush=True)
        if backend == "groq":
            payload = call_groq(audio, api_key, model or DEFAULT_GROQ_MODEL, language)
        else:
            payload = call_scribe(audio, api_key, language, num_speakers)

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with Groq Whisper or ElevenLabs Scribe")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers when using ElevenLabs. Groq ignores this.",
    )
    ap.add_argument(
        "--backend",
        choices=["groq", "elevenlabs"],
        default=None,
        help="Transcription backend. Default: TRANSCRIPTION_BACKEND, else Groq when GROQ_API_KEY exists.",
    )
    ap.add_argument(
        "--groq-model",
        default=None,
        help=f"Groq transcription model (default: {DEFAULT_GROQ_MODEL}).",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    backend, api_key, model = load_transcription_config()
    if args.backend:
        backend = args.backend
        if backend == "groq":
            api_key = load_env_values().get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY", "")
            model = args.groq_model or model or DEFAULT_GROQ_MODEL
        else:
            api_key = load_env_values().get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY", "")
            model = None
        if not api_key:
            sys.exit(f"{backend.upper()} API key not found in .env or environment")
    elif args.groq_model:
        model = args.groq_model

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        backend=backend,
        model=model,
    )


if __name__ == "__main__":
    main()
