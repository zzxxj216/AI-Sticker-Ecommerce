"""Text-to-speech services for the V2 pipeline."""

from src.services.tts.service import ElevenLabsTTS, get_tts_service

__all__ = ["ElevenLabsTTS", "get_tts_service"]
