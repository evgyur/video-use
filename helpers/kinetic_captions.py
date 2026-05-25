"""Render kinetic word-by-word captions as a transparent overlay.

The helper reads an EDL plus cached word-level transcripts and produces a
transparent QuickTime overlay (`qtrle` / `argb`). Add the resulting file to the
EDL `overlays` array and render normally:

    python helpers/kinetic_captions.py edit/edl.json -o edit/kinetic_captions.mov

The default style is optimized for vertical social clips: 1-2 words reveal at
a time, up to 5 words stay on screen, then the group clears.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


DEFAULT_STYLE: dict[str, Any] = {
    "width": 1080,
    "height": 1920,
    "fps": 24,
    "max_visible_words": 5,
    "max_lines": 2,
    "center_y": 1510,
    "max_block_width": 780,
    "font": "DejaVuSans-Bold.ttf",
    "case": "uppercase",
    "base_font_px": 82,
    "emphasis_scale": 1.12,
    "line_height": 0.9,
    "word_spacing_px": 18,
    "fill": "#F7F7F2",
    "settling_fill": "#CFCFC8",
    "stroke": "#050505",
    "stroke_px": 7,
    "shadow_opacity": 0.55,
    "entry_ms": 140,
    "exit_ms": 110,
    "group_gap_s": 0.45,
    "protective_gradient": {
        "enabled": True,
        "bottom_opacity": 0.52,
        "fade_to_y_ratio": 0.60,
    },
}


LEADING_PUNCT_RE = re.compile(r"^[\s\"'([{<]+")
TRAILING_PUNCT_RE = re.compile(r"[\s\"')\]}>.,!?;:]+$")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_style(path: Path | None) -> dict[str, Any]:
    if not path:
        return DEFAULT_STYLE
    return deep_merge(DEFAULT_STYLE, json.loads(path.read_text(encoding="utf-8-sig")))


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected #RRGGBB color, got {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def resolve_path(maybe_path: str, base: Path) -> Path:
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def clean_word(raw: str, case: str) -> str:
    word = LEADING_PUNCT_RE.sub("", raw or "")
    word = TRAILING_PUNCT_RE.sub("", word).strip()
    if not word:
        return ""
    if case == "uppercase":
        return word.upper()
    if case == "lowercase":
        return word.lower()
    return word


def words_in_range(transcript: dict[str, Any], start: float, end: float, case: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for w in transcript.get("words", []):
        if w.get("type", "word") != "word":
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None or we is None or we <= start or ws >= end:
            continue
        text = clean_word(w.get("text") or w.get("word") or "", case)
        if not text:
            continue
        out.append({"text": text, "start": float(ws), "end": float(we)})
    return out


def output_timeline_words(edl: dict[str, Any], edit_dir: Path, style: dict[str, Any]) -> list[dict[str, Any]]:
    transcripts_dir = edit_dir / "transcripts"
    words: list[dict[str, Any]] = []
    seg_offset = 0.0
    case = str(style.get("case", "uppercase")).lower()

    for r in edl["ranges"]:
        src_name = r["source"]
        seg_start = float(r["start"])
        seg_end = float(r["end"])
        seg_duration = max(0.0, seg_end - seg_start)
        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            print(f"warning: no transcript for {src_name}; skipping kinetic captions")
            seg_offset += seg_duration
            continue
        transcript = json.loads(tr_path.read_text(encoding="utf-8-sig"))
        for w in words_in_range(transcript, seg_start, seg_end, case):
            start = max(seg_start, w["start"])
            end = min(seg_end, w["end"])
            out_start = max(0.0, start - seg_start) + seg_offset
            out_end = max(out_start + 0.08, max(0.0, end - seg_start) + seg_offset)
            words.append({"text": w["text"], "start": out_start, "end": out_end})
        seg_offset += seg_duration
    return words


def build_groups(words: list[dict[str, Any]], style: dict[str, Any]) -> list[list[dict[str, Any]]]:
    max_words = int(style["max_visible_words"])
    group_gap = float(style["group_gap_s"])
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    prev_end: float | None = None
    for word in words:
        gap = 0.0 if prev_end is None else float(word["start"]) - prev_end
        if cur and (len(cur) >= max_words or gap > group_gap):
            groups.append(cur)
            cur = []
        cur.append(word)
        prev_end = float(word["end"])
    if cur:
        groups.append(cur)
    return groups


def total_duration(edl: dict[str, Any], words: list[dict[str, Any]]) -> float:
    if edl.get("total_duration_s"):
        return float(edl["total_duration_s"])
    duration = sum(max(0.0, float(r["end"]) - float(r["start"])) for r in edl["ranges"])
    if words:
        duration = max(duration, max(float(w["end"]) for w in words))
    return duration


def load_font(font_name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(font_name, size=size)
    except OSError:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
        except OSError:
            return ImageFont.load_default()


def word_font(style: dict[str, Any], word: str, scale: float = 1.0) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    base = int(style["base_font_px"])
    emph_words = {str(w).upper() for w in style.get("emphasis_words", [])}
    emph = float(style["emphasis_scale"]) if word.upper() in emph_words else 1.0
    return load_font(str(style["font"]), max(12, int(base * emph * scale)))


def text_width(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, text: str) -> int:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=0)
    return box[2] - box[0]


def layout_lines(
    draw: ImageDraw.ImageDraw,
    words: list[str],
    style: dict[str, Any],
) -> tuple[list[list[tuple[str, ImageFont.ImageFont]]], float]:
    max_width = int(style["max_block_width"])
    max_lines = int(style["max_lines"])
    spacing_base = int(style["word_spacing_px"])

    for scale in [1.0, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72]:
        word_items = [(w, word_font(style, w, scale)) for w in words]
        lines: list[list[tuple[str, ImageFont.ImageFont]]] = [[]]
        widths = [0]
        for item in word_items:
            word, font = item
            width = text_width(draw, font, word)
            add = width if not lines[-1] else width + int(spacing_base * scale)
            if lines[-1] and widths[-1] + add > max_width and len(lines) < max_lines:
                lines.append([item])
                widths.append(width)
            else:
                lines[-1].append(item)
                widths[-1] += add
        if max(widths) <= max_width:
            return lines, scale
    return lines, 0.72


def gradient_layer(style: dict[str, Any]) -> Image.Image:
    width = int(style["width"])
    height = int(style["height"])
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    grad = style.get("protective_gradient") or {}
    if not grad.get("enabled", True):
        return img
    start_y = int(height * float(grad.get("fade_to_y_ratio", 0.60)))
    max_alpha = int(255 * float(grad.get("bottom_opacity", 0.52)))
    px = img.load()
    for y in range(start_y, height):
        t = (y - start_y) / max(1, height - start_y)
        alpha = int(max_alpha * (t ** 1.35))
        for x in range(width):
            px[x, y] = (0, 0, 0, alpha)
    return img


def draw_caption(
    img: Image.Image,
    group: list[dict[str, Any]],
    t: float,
    style: dict[str, Any],
    next_start: float | None,
) -> None:
    draw = ImageDraw.Draw(img)
    visible = [w for w in group if t >= float(w["start"]) - 0.02]
    if not visible:
        return

    if next_start is not None and t > next_start - float(style["exit_ms"]) / 1000:
        group_alpha = max(0.0, (next_start - t) / (float(style["exit_ms"]) / 1000))
    else:
        group_alpha = 1.0

    lines, scale = layout_lines(draw, [w["text"] for w in visible], style)
    line_gap = int(8 * scale)
    line_heights = [max(getattr(font, "size", int(style["base_font_px"])) for _word, font in line) for line in lines]
    total_h = sum(line_heights) + line_gap * (len(lines) - 1)
    y = int(float(style["center_y"]) - total_h / 2)

    fill_rgb = hex_to_rgb(str(style["fill"]))
    settle_rgb = hex_to_rgb(str(style["settling_fill"]))
    stroke_rgb = hex_to_rgb(str(style["stroke"]))
    stroke_px = int(style["stroke_px"])
    shadow_opacity = float(style["shadow_opacity"])
    entry_s = float(style["entry_ms"]) / 1000
    word_index = 0

    for line, line_h in zip(lines, line_heights):
        spacing = int(int(style["word_spacing_px"]) * scale)
        line_w = sum(text_width(draw, font, word) for word, font in line) + spacing * (len(line) - 1)
        x = int((int(style["width"]) - line_w) / 2)
        for word, font in line:
            source = visible[word_index]
            age = t - float(source["start"])
            enter = ease_out_cubic(age / entry_s)
            alpha = int(255 * group_alpha * enter)
            settle = ease_out_cubic(age / 0.18)
            fill = tuple(int(a + (b - a) * settle) for a, b in zip(settle_rgb, fill_rgb))
            y_offset = int((1 - enter) * 10)
            shadow = (0, 0, 0, int(alpha * shadow_opacity))
            draw.text((x + 3, y + y_offset + 4), word, font=font, fill=shadow, stroke_width=stroke_px, stroke_fill=shadow)
            draw.text((x, y + y_offset), word, font=font, fill=(*fill, alpha), stroke_width=stroke_px, stroke_fill=(*stroke_rgb, alpha))
            x += text_width(draw, font, word) + spacing
            word_index += 1
        y += line_h + line_gap


def render(edl_path: Path, out_path: Path, style_path: Path | None) -> None:
    edit_dir = edl_path.parent
    style = load_style(style_path)
    edl = json.loads(edl_path.read_text(encoding="utf-8-sig"))
    words = output_timeline_words(edl, edit_dir, style)
    groups = build_groups(words, style)
    starts = [float(g[0]["start"]) for g in groups]
    duration = total_duration(edl, words)
    fps = int(style["fps"])
    width = int(style["width"])
    height = int(style["height"])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        "-an",
        "-c:v", "qtrle",
        "-pix_fmt", "argb",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    gradient = gradient_layer(style)
    group_idx = 0
    total_frames = int(math.ceil(duration * fps))
    for frame in range(total_frames):
        t = frame / fps
        while group_idx + 1 < len(groups) and starts[group_idx + 1] <= t:
            group_idx += 1
        img = gradient.copy()
        if groups:
            next_start = starts[group_idx + 1] if group_idx + 1 < len(groups) else None
            draw_caption(img, groups[group_idx], t, style, next_start)
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    code = proc.wait()
    if code != 0:
        raise SystemExit(code)
    print(f"kinetic captions -> {out_path} ({len(groups)} groups)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Render kinetic captions as a transparent overlay")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output .mov path")
    ap.add_argument("--style", type=Path, help="Optional caption style JSON")
    args = ap.parse_args()
    render(args.edl.resolve(), args.output.resolve(), args.style.resolve() if args.style else None)


if __name__ == "__main__":
    main()
