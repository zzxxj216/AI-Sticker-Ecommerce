"""One-off: generate the AI-sticker promo voiceover via the existing
ElevenLabs TTS service. Writes mp3 + (optional) SRT to output/voiceover/.

Run: python scripts/gen_voiceover.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.tts import get_tts_service
from src.services.video_narration.media import (
    seg_times_from_alignment, build_srt,
)

# Script segments (one subtitle line each), in spoken order.
SEGMENTS = [
    "I gave AI just one photo of me…",
    "and asked it to turn me into a fashion sticker card.",
    "A few seconds later, this came out.",
    "Then I printed it, peeled it off…",
    "and turned my phone case into something totally personal.",
    "Want one? Comment “sticker”.",
]

OUT_DIR = Path("output/voiceover")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    full_text = " ".join(SEGMENTS)
    tts = get_tts_service()
    print(f"voice={tts.voice_id} model={tts.model_id}")
    print(f"chars={len(full_text)}")

    audio, align = tts.synthesize_with_timestamps(
        full_text, task="tts:promo_voiceover",
    )
    mp3 = OUT_DIR / "ai_sticker_promo_v2.mp3"
    mp3.write_bytes(audio)
    print(f"wrote {mp3} ({len(audio)} bytes)")

    if align:
        seg_times = seg_times_from_alignment(SEGMENTS, align)
        if seg_times:
            srt = OUT_DIR / "ai_sticker_promo_v2.srt"
            srt.write_text(build_srt(seg_times), encoding="utf-8")
            print(f"wrote {srt}")
            print(f"spoken duration ~= {seg_times[-1][1]:.2f}s")
        else:
            print("alignment present but could not map segments -> no SRT")
    else:
        print("no alignment returned")


if __name__ == "__main__":
    main()
