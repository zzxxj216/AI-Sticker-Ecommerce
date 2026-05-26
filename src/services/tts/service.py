"""ElevenLabs text-to-speech client (REST via ``requests`` — no extra SDK).

Provides ``synthesize_with_timestamps`` which returns the mp3 bytes plus
ElevenLabs' character-level alignment, used by the video-narration feature to
build subtitles that sync to the actual spoken audio. Every call is recorded
in ``ai_call_logs`` via :class:`AICallLog`.

Config (env / .env):
    ELEVENLABS_API_KEY    required
    ELEVENLABS_VOICE_ID   default voice (else built-in free-tier default)
    ELEVENLABS_MODEL_ID   default model (eleven_multilingual_v2)
"""

from __future__ import annotations

import base64
from typing import Optional

import requests

from src.core.config import config
from src.core.exceptions import APIError
from src.core.logger import get_logger
from src.services.ai.call_logger import AICallLog
from src.services.ai.cost import estimate_tts_cost

logger = get_logger("service.tts.elevenlabs")

_API_ROOT = "https://api.elevenlabs.io/v1"
# "Jessica" — a current premade en-US voice usable on the free tier via API.
_DEFAULT_VOICE_ID = "cgSgspJ2msm6clMCkdW9"
_DEFAULT_MODEL_ID = "eleven_multilingual_v2"
_DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsTTS:
    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        timeout: int = 180,
    ) -> None:
        self.api_key = api_key or config.elevenlabs_api_key
        self.voice_id = voice_id or config.elevenlabs_voice_id or _DEFAULT_VOICE_ID
        self.model_id = model_id or config.elevenlabs_model_id or _DEFAULT_MODEL_ID
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        task: str = "tts:segment",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> bytes:
        """Synthesize ``text`` to mp3 bytes (no timestamps — used for per-segment
        clips that get positioned on the timeline by duration). Raises APIError."""
        if not self.is_configured():
            raise APIError(
                "ElevenLabs API key not configured. Set ELEVENLABS_API_KEY in .env",
                service="elevenlabs", status_code=401,
            )
        clean = (text or "").strip()
        if not clean:
            raise APIError("TTS synthesize: empty text", service="elevenlabs")
        vid = voice_id or self.voice_id
        mid = model_id or self.model_id
        url = f"{_API_ROOT}/text-to-speech/{vid}"
        with AICallLog(
            service="elevenlabs", model=mid, task=task,
            related_table=related_table, related_id=related_id,
            prompt_summary=clean[:500],
        ) as log:
            resp = requests.post(
                url,
                params={"output_format": output_format},
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json",
                         "Accept": "audio/mpeg"},
                json={"text": clean, "model_id": mid,
                      "voice_settings": {"stability": stability,
                                         "similarity_boost": similarity_boost}},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                raise APIError(
                    f"ElevenLabs TTS HTTP {resp.status_code}: {resp.text[:300]}",
                    service="elevenlabs", status_code=resp.status_code,
                )
            audio = resp.content
            if not audio:
                raise APIError("ElevenLabs TTS returned empty audio", service="elevenlabs")
            log.set_usage(input_tokens=len(clean), cost=estimate_tts_cost(mid, len(clean)))
            return audio

    def synthesize_with_timestamps(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        task: str = "tts:with_timestamps",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> tuple[bytes, Optional[dict]]:
        """Synthesize ``text`` to mp3. Returns (audio_bytes, alignment|None).

        ``alignment`` is ElevenLabs' char-level timing dict (characters +
        start/end second arrays). Raises ``APIError`` on failure.
        """
        if not self.is_configured():
            raise APIError(
                "ElevenLabs API key not configured. Set ELEVENLABS_API_KEY in .env",
                service="elevenlabs", status_code=401,
            )
        clean = (text or "").strip()
        if not clean:
            raise APIError("TTS synthesize: empty text", service="elevenlabs")

        vid = voice_id or self.voice_id
        mid = model_id or self.model_id
        url = f"{_API_ROOT}/text-to-speech/{vid}/with-timestamps"

        with AICallLog(
            service="elevenlabs", model=mid, task=task,
            related_table=related_table, related_id=related_id,
            prompt_summary=clean[:500],
        ) as log:
            resp = requests.post(
                url,
                params={"output_format": output_format},
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "text": clean, "model_id": mid,
                    "voice_settings": {"stability": stability,
                                       "similarity_boost": similarity_boost},
                },
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                raise APIError(
                    f"ElevenLabs TTS HTTP {resp.status_code}: {resp.text[:300]}",
                    service="elevenlabs", status_code=resp.status_code,
                )
            j = resp.json()
            audio = base64.b64decode(j["audio_base64"])
            if not audio:
                raise APIError("ElevenLabs TTS returned empty audio", service="elevenlabs")
            log.set_usage(input_tokens=len(clean), cost=estimate_tts_cost(mid, len(clean)))
            return audio, j.get("alignment")


_svc: Optional[ElevenLabsTTS] = None


def get_tts_service() -> ElevenLabsTTS:
    global _svc
    if _svc is None:
        _svc = ElevenLabsTTS()
    return _svc
