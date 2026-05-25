"""Pick a background music track from a folder with simple round-robin history.

This helper does not download or vendor music. It only chooses from files the
user already provided.

Usage:
    python helpers/select_music.py edit/music --history edit/music-history.json
    python helpers/select_music.py edit/music --mood warm --json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def load_history(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return {"last_track": None, "uses": []}


def choose_track(music_dir: Path, mood: str | None, history: dict) -> Path:
    tracks = sorted(p for p in music_dir.iterdir() if p.is_file() and p.suffix.lower() in EXTS)
    if mood:
        mood_l = mood.lower()
        mood_tracks = [p for p in tracks if mood_l in p.stem.lower()]
        if mood_tracks:
            tracks = mood_tracks
    if not tracks:
        raise SystemExit(f"no music files found in {music_dir}")

    last = history.get("last_track")
    for track in tracks:
        if track.name != last:
            return track
    return tracks[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="Select a background music track")
    ap.add_argument("music_dir", type=Path)
    ap.add_argument("--history", type=Path, default=Path("music-history.json"))
    ap.add_argument("--mood")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of only the path")
    args = ap.parse_args()

    history = load_history(args.history)
    track = choose_track(args.music_dir, args.mood, history)
    history["last_track"] = track.name
    history.setdefault("uses", []).append({
        "track": track.name,
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "mood": args.mood,
    })
    args.history.parent.mkdir(parents=True, exist_ok=True)
    args.history.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps({"file": str(track), "track": track.name}, ensure_ascii=False))
    else:
        print(track)


if __name__ == "__main__":
    main()
