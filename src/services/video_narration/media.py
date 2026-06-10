"""Media helpers for video narration: ffprobe, SRT/ASS subtitle building,
audio fit (atempo), and the final burn+mux. All shell out to ffmpeg/ffprobe
(system dependency — same tools the operator already uses).

Ported from the validated scratch prototype.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from src.core.logger import get_logger

logger = get_logger("service.video_narration.media")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def make_gemini_proxy(
    src: Path,
    out: Path,
    *,
    max_height: int = 720,
    video_bitrate: str = "1M",
    audio_bitrate: str = "64k",
) -> Path:
    """Downscale/re-encode for Gemini video understanding (smaller upload)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
            "-i", str(src),
            "-vf", f"scale=-2:{max_height}",
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", video_bitrate,
            "-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart",
            str(out),
        ],
        check=True,
    )
    logger.info(
        "gemini proxy %s: %.1fMB -> %.1fMB",
        src.name,
        src.stat().st_size / 1024 / 1024,
        out.stat().st_size / 1024 / 1024,
    )
    return out


def video_dims(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        w, h = out.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


# ----------------------------------------------------------------------
# Subtitle timing (prefer real speech alignment; fall back to planned)
# ----------------------------------------------------------------------

def seg_times_from_alignment(segment_texts: list[str], align: dict):
    """Map each narration segment to (start_s, end_s, text) via ElevenLabs'
    char-level timestamps. Returns None if it can't align cleanly."""
    try:
        chars = align["characters"]
        starts = align["character_start_times_seconds"]
        ends = align["character_end_times_seconds"]
    except Exception:
        return None
    full = "".join(chars)
    out, cursor = [], 0
    for seg in segment_texts:
        s = seg.strip()
        if not s:
            continue
        i = full.find(s, cursor)
        if i < 0:
            probe = s[: max(6, len(s) // 2)]
            i = full.find(probe, cursor)
            if i < 0:
                return None
            j = min(i + len(probe) - 1, len(ends) - 1)
        else:
            j = min(i + len(s) - 1, len(ends) - 1)
        out.append((float(starts[i]), float(ends[j]), s))
        cursor = j + 1
    return out or None


def _srt_ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(seg_times: list[tuple]) -> str:
    """seg_times: list of (start_s, end_s, text) — already on the final timeline."""
    lines = []
    for i, (st, en, txt) in enumerate(seg_times, 1):
        lines += [str(i), f"{_srt_ts(st)} --> {_srt_ts(en)}", str(txt).strip(), ""]
    return "\n".join(lines)


def _ass_ts(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass(seg_times: list[tuple], w: int, h: int, out_path: Path) -> None:
    """Generate a styled .ass with an explicit PlayRes matching the video, so
    font size / margins / alignment are all in real pixels (no libass default-
    resolution surprises). Tuned for 9:16 vertical: bottom-center, ~20% up."""
    H = h or 1920
    W = w or 1080
    fontsize = max(24, round(W * 0.045))   # ~4.5% of width
    marginv = round(H * 0.20)              # ~20% up from the bottom (above TikTok UI)
    side = round(W * 0.08)                 # L/R margins -> wrap long lines
    outline = max(2, round(W * 0.004))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{fontsize},&H00FFFFFF,&H000000FF,&H00000000,"
        f"&H64000000,1,0,0,0,100,100,0,0,1,{outline},2,2,{side},{side},{marginv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    rows = []
    for st, en, txt in seg_times:
        txt = str(txt).replace("\n", " ").strip()
        rows.append(f"Dialogue: 0,{_ass_ts(st)},{_ass_ts(en)},Default,,0,0,0,,{txt}")
    out_path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
# Audio fit + final compose
# ----------------------------------------------------------------------

def fit_audio(audio_in: Path, target_dur: float, audio_out: Path) -> float:
    """One-sided fit: only COMPRESS when the voiceover would overrun the video
    (so it never spills past the last frame). If shorter, keep natural pace —
    the voiceover simply ends before the video does. Returns the atempo factor
    applied (1.0 = none)."""
    actual = probe_duration(audio_in)
    if actual <= 0 or target_dur <= 0:
        shutil.copy(audio_in, audio_out)
        return 1.0
    if actual <= target_dur + 0.15:
        shutil.copy(audio_in, audio_out)
        return 1.0
    factor = min(2.0, actual / target_dur)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(audio_in),
         "-filter:a", f"atempo={factor:.4f}", "-c:a", "libmp3lame", "-q:a", "2",
         str(audio_out)],
        check=True,
    )
    return factor


def atempo_clip(in_path: Path, out_path: Path, factor: float) -> None:
    """Speed-change a single clip (atempo). factor clamped to [0.5, 2.0]."""
    f = max(0.5, min(2.0, factor))
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(in_path),
         "-filter:a", f"atempo={f:.4f}", "-c:a", "libmp3lame", "-q:a", "2", str(out_path)],
        check=True,
    )


def assemble_positioned_audio(placed: list[tuple], total_dur: float, out_path: Path) -> None:
    """Lay clips on a timeline at their start times (silence between), padded/
    trimmed to total_dur. ``placed`` = list of (clip_path, start_s).

    Each clip is delayed to its start with adelay, then summed (amix,
    normalize=0 — clips don't overlap so this just places each at full volume).
    """
    if not placed:
        raise RuntimeError("assemble_positioned_audio: no clips")
    inputs: list[str] = []
    for cp, _ in placed:
        inputs += ["-i", str(cp)]
    parts, labels = [], []
    for idx, (_, start) in enumerate(placed):
        ms = int(round(max(0.0, start) * 1000))
        parts.append(f"[{idx}]adelay=delays={ms}:all=1[a{idx}]")
        labels.append(f"[a{idx}]")
    n = len(placed)
    parts.append(
        "".join(labels) + f"amix=inputs={n}:normalize=0,apad,atrim=0:{total_dur:.3f}[out]"
    )
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg audio assemble failed: {r.stderr[-500:]}")


def burn_and_mux(video: Path, voiceover_name: str, ass_name: str,
                 out_name: str, *, cwd: Path) -> None:
    """Full video + fitted voiceover + burned .ass subtitles -> out_name.
    Runs with cwd so the ass filter takes a simple relative path (avoids
    Windows drive-colon escaping in the filtergraph). Raises on failure."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video.resolve()),
        "-i", voiceover_name,
        "-vf", f"ass={ass_name}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-c:a", "aac",
        out_name,
    ]
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg compose failed: {r.stderr[-500:]}")


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
