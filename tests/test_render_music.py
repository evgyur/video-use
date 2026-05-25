from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDER_PATH = REPO_ROOT / "helpers" / "render.py"
spec = importlib.util.spec_from_file_location("render", RENDER_PATH)
assert spec and spec.loader
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


class RenderMusicTests(unittest.TestCase):
    def test_music_only_input_does_not_reference_missing_voice_audio(self) -> None:
        captured: list[str] = []

        def fake_run_quiet(cmd: list[str]) -> None:
            captured.extend(cmd)

        old_has_audio_stream = getattr(render, "has_audio_stream")
        old_media_duration = getattr(render, "media_duration")
        old_run_quiet = getattr(render, "run_quiet")
        try:
            setattr(render, "has_audio_stream", lambda _path: False)
            setattr(render, "media_duration", lambda _path: 1.0)
            setattr(render, "run_quiet", fake_run_quiet)

            with tempfile.TemporaryDirectory() as tmp:
                edit_dir = Path(tmp)
                base = edit_dir / "base.mp4"
                music = edit_dir / "music.mp3"
                out = edit_dir / "out.mp4"
                base.touch()
                music.touch()

                render.build_final_composite(
                    base,
                    overlays=[],
                    subtitles_path=None,
                    music={"file": str(music), "duck_under_voice": True},
                    out_path=out,
                    edit_dir=edit_dir,
                )

        finally:
            setattr(render, "has_audio_stream", old_has_audio_stream)
            setattr(render, "media_duration", old_media_duration)
            setattr(render, "run_quiet", old_run_quiet)

        cmd_text = " ".join(captured)
        self.assertIn("[music]anull[outa]", cmd_text)
        self.assertIn("-map [outa]", cmd_text)
        self.assertNotIn("[0:a]", cmd_text)
        self.assertNotIn("[voice]", cmd_text)


if __name__ == "__main__":
    unittest.main()
