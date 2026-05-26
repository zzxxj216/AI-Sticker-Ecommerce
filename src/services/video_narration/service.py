"""Video narration service — silent video -> Gemini analysis -> English
voiceover (ElevenLabs) + synced burned-in subtitles -> narrated mp4.

The voiceover is built per-segment and each line is PLACED at the scene it
describes (anchored timeline), so audio/subtitles never drift more than ~1s
from the matching shot. Lines too long for their scene window are speed-fitted
(atempo, capped 2x); anything that hits the cap, drifts >1s, leaves too much
dead air, or overruns the video is flagged ``needs_review`` for offline eval.

Reuses existing infra: GeminiService (video understanding), ElevenLabs TTS,
PackStore (file layout), AICallLog (cost tracking). Files land under
output/packs/{pack_uid}/videos/{video_id}/narration/.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.config import config
from src.core.logger import get_logger
from src.services.ai.call_logger import AICallLog
from src.services.ai.cost import estimate_text_cost
from src.services.ai.gemini_service import GeminiService
from src.services.storage.pack_store import PackStore, get_pack_store
from src.services.tts import get_tts_service
from src.services.video_narration import media
from src.services.video_narration.prompts import build_analysis_prompt

logger = get_logger("service.video_narration")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# Quality thresholds — exceeding any flags the row needs_review.
_DRIFT_LIMIT_S = 1.0       # a line may start at most this late vs its scene anchor
_ATEMPO_WARN = 1.6         # speed-up beyond this sounds rushed
_MIN_COVERAGE = 0.40       # voiceover should cover at least this fraction of the video
_OVERRUN_LIMIT_S = 1.0     # total voiceover may exceed the video by at most this


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> int:
    return int(time.time())


class VideoNarrationService:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        store: Optional[PackStore] = None,
    ) -> None:
        self.db_path = db_path
        self.store = store or get_pack_store()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        try:
            d["analysis"] = json.loads(d.get("analysis_json") or "{}")
        except Exception:
            d["analysis"] = {}
        try:
            d["segments"] = json.loads(d.get("segments_json") or "[]")
        except Exception:
            d["segments"] = []
        try:
            d["quality"] = json.loads(d.get("quality_json") or "{}")
        except Exception:
            d["quality"] = {}
        return d

    def list_narrations(self, video_id: int) -> list[dict]:
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM tk_video_narrations WHERE video_id=? ORDER BY id DESC",
                (video_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_narration(self, narration_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                "SELECT * FROM tk_video_narrations WHERE id=?", (narration_id,),
            ).fetchone()
        return self._row_to_dict(r) if r else None

    def latest_for_video(self, video_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                "SELECT * FROM tk_video_narrations WHERE video_id=? ORDER BY id DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        return self._row_to_dict(r) if r else None

    # ------------------------------------------------------------------
    # Pack metadata ("弹药" for the script — pulled from existing tables)
    # ------------------------------------------------------------------

    def _gather_meta(self, video_id: int) -> tuple[Path, str, dict]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """SELECT v.local_video_path, v.video_one_liner,
                          p.id AS pack_id, p.pack_uid, p.display_name, p.total_stickers,
                          s.style_anchor, s.palette, s.pack_archetype, s.metadata_json
                     FROM tk_videos v
                LEFT JOIN packs p       ON p.id = v.pack_id
                LEFT JOIN pack_series s ON s.id = p.series_id
                    WHERE v.id = ?""",
                (video_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"video #{video_id} not found")
            if not row["pack_uid"]:
                raise ValueError("pack_uid not found for this video")
            sku = conn.execute(
                "SELECT seller_sku FROM tkshop_products WHERE pack_id=? ORDER BY id DESC LIMIT 1",
                (row["pack_id"],),
            ).fetchone()

        themes: list[str] = []
        hot_topic = ""
        try:
            md = json.loads(row["metadata_json"] or "{}")
            for pb in (md.get("preview_briefs") or [])[:3]:
                themes.extend((pb.get("stickers") or [])[:4])
            hot_topic = md.get("trend_topic") or md.get("hot_topic") or ""
        except Exception:
            pass

        meta = {
            "display_name": row["display_name"] or "",
            "archetype": row["pack_archetype"] or "",
            "style_anchor": row["style_anchor"] or "",
            "palette": row["palette"] or "",
            "themes": themes,
            "hot_topic": hot_topic or (row["video_one_liner"] or ""),
            "seller_sku": (sku["seller_sku"] if sku else "") or "",
        }
        local = Path(row["local_video_path"] or "")
        return local, row["pack_uid"], meta

    # ------------------------------------------------------------------
    # Generate (the full pipeline)
    # ------------------------------------------------------------------

    def generate(self, video_id: int, *, voice_id: Optional[str] = None) -> dict[str, Any]:
        local, pack_uid, meta = self._gather_meta(video_id)
        if not local.is_file() or local.stat().st_size < 1024:
            raise ValueError("video file missing/empty — upload the silent video first")

        tts = get_tts_service()
        if not tts.is_configured():
            raise ValueError("ELEVENLABS_API_KEY 未配置 — 请在 .env 中设置")
        voice = voice_id or tts.voice_id
        gemini_model = config.gemini_text_model

        dur = media.probe_duration(local)
        w, h = media.video_dims(local)
        if dur <= 0:
            raise ValueError("could not read video duration (ffprobe)")

        nid = self._insert(video_id, gemini_model, voice, tts.model_id, dur)
        try:
            # 1) Gemini analysis -> structured, scene-anchored narration
            gem = GeminiService(model=gemini_model)
            prompt = build_analysis_prompt(dur, meta=meta)
            with AICallLog(
                service="gemini", model=gemini_model, task="narration:analyze",
                related_table="tk_video_narrations", related_id=nid,
                prompt_summary=prompt[:300],
            ) as log:
                res = gem.analyze_video(local, prompt, json_mode=True)
                u = res.get("usage") or {}
                log.set_usage(
                    input_tokens=u.get("input_tokens", 0),
                    output_tokens=u.get("output_tokens", 0),
                    cost=estimate_text_cost(gemini_model, u.get("input_tokens", 0),
                                            u.get("output_tokens", 0)),
                )
            data = media.parse_json(res.get("text") or "{}")

            raw_segs = [
                (float(s.get("start_s") or 0), s.get("text") or "")
                for s in (data.get("narration_segments") or [])
                if (s.get("text") or "").strip()
            ]
            if not raw_segs:
                summary = (data.get("summary") or "").strip()
                if not summary:
                    raise ValueError("Gemini returned no narration text")
                raw_segs = [(0.0, summary)]

            # 2) Anchor each segment to its scene start (non-decreasing, within video)
            anchors: list[float] = []
            prev = 0.0
            for st, _ in raw_segs:
                a = min(max(st, prev), max(0.0, dur - 0.3))
                anchors.append(a)
                prev = a

            ndir = self.store.narration_dir(pack_uid, video_id)
            # 3) Per-segment TTS, fit each into its scene window
            warnings: list[str] = []
            max_atempo = 1.0
            clips: list[tuple[Path, float, str]] = []   # (path, dur, text)
            for i, (a, (_, text)) in enumerate(zip(anchors, raw_segs)):
                a_next = anchors[i + 1] if i + 1 < len(anchors) else dur
                window = max(0.4, a_next - a)
                audio = tts.synthesize(
                    text.strip(), voice_id=voice, task="narration:tts",
                    related_table="tk_video_narrations", related_id=nid,
                )
                cp = ndir / f"seg_{i}.mp3"
                cp.write_bytes(audio)
                d = media.probe_duration(cp)
                if d > window:
                    factor = d / window
                    media.atempo_clip(cp, ndir / f"seg_{i}_f.mp3", factor)
                    (ndir / f"seg_{i}_f.mp3").replace(cp)
                    d = media.probe_duration(cp)
                    max_atempo = max(max_atempo, min(2.0, factor))
                    if factor >= 1.99:
                        warnings.append(f"段{i} 语速顶满(2x)仍超出镜头窗")
                    elif factor >= _ATEMPO_WARN:
                        warnings.append(f"段{i} 语速压缩到 {factor:.2f}x（偏快）")
                clips.append((cp, d, text.strip()))

            # 4) Greedy placement (start at anchor, never overlap previous)
            placements: list[tuple[Path, float, float, str]] = []
            prev_end = 0.0
            max_drift = 0.0
            for a, (cp, d, text) in zip(anchors, clips):
                start = max(a, prev_end)
                max_drift = max(max_drift, start - a)
                placements.append((cp, start, start + d, text))
                prev_end = start + d

            coverage = (sum(d for _, d, _ in clips) / dur) if dur else 0.0
            if max_drift > _DRIFT_LIMIT_S:
                warnings.append(f"最大镜头漂移 {max_drift:.1f}s（>{_DRIFT_LIMIT_S:.0f}s）")
            if coverage < _MIN_COVERAGE:
                warnings.append(f"配音覆盖率 {coverage:.0%} 偏低（留白偏多）")
            if prev_end > dur + _OVERRUN_LIMIT_S:
                warnings.append(f"配音总长超出视频 {prev_end - dur:.1f}s")
            needs_review = 1 if warnings else 0

            # 5) Assemble positioned voiceover + subtitles (final timeline)
            voiceover = ndir / "voiceover.mp3"
            media.assemble_positioned_audio(
                [(cp, start) for cp, start, _, _ in placements], dur, voiceover)
            for cp, _, _, _ in placements:   # tidy per-segment clips
                try:
                    cp.unlink()
                except Exception:
                    pass

            seg_times = [(start, end, text) for _, start, end, text in placements]
            srt = media.build_srt(seg_times)
            (ndir / "subtitles.srt").write_text(srt, encoding="utf-8")
            media.write_ass(seg_times, w, h, ndir / "subtitles.ass")
            (ndir / "analysis.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            # 6) Compose narrated mp4
            media.burn_and_mux(local, "voiceover.mp3", "subtitles.ass",
                               "narrated.mp4", cwd=ndir)
            narrated = ndir / "narrated.mp4"
            narration = " ".join(t for _, _, t in seg_times).strip()
            quality = {
                "max_atempo": round(max_atempo, 3),
                "max_drift_s": round(max_drift, 3),
                "coverage": round(coverage, 3),
                "n_segments": len(clips),
            }

            self._finish(
                nid, status="done",
                product_name=data.get("product_name", ""),
                summary=data.get("summary", ""),
                narration_text=narration,
                analysis_json=json.dumps(data, ensure_ascii=False),
                segments_json=json.dumps(
                    [{"start_s": st, "end_s": en, "text": tx} for st, en, tx in seg_times],
                    ensure_ascii=False),
                srt_text=srt,
                audio_path=voiceover.as_posix(),
                video_path=narrated.as_posix(),
                atempo=max_atempo,
                needs_review=needs_review,
                warnings="; ".join(warnings),
                quality_json=json.dumps(quality, ensure_ascii=False),
            )
            logger.info("narration #%s done (video #%s, drift=%.2fs, atempo=%.2f, review=%d)",
                        nid, video_id, max_drift, max_atempo, needs_review)
        except Exception as e:  # noqa: BLE001
            logger.exception("narration #%s failed (video #%s)", nid, video_id)
            self._finish(nid, status="error", error=f"{type(e).__name__}: {e}"[:1000])
            raise
        return self.get_narration(nid)

    # ------------------------------------------------------------------
    # Promote narrated mp4 into the publish slot (reuse Blotato dispatch)
    # ------------------------------------------------------------------

    def promote_to_publish(self, narration_id: int) -> bool:
        import shutil
        n = self.get_narration(narration_id)
        if not n or n.get("status") != "done":
            raise ValueError("narration not ready")
        narrated = Path(n.get("video_path") or "")
        if not narrated.is_file():
            raise ValueError("narrated mp4 missing")
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                "SELECT local_video_path FROM tk_videos WHERE id=?", (n["video_id"],),
            ).fetchone()
            if not r or not r["local_video_path"]:
                raise ValueError("video has no local_video_path slot")
            dest = Path(r["local_video_path"])
            shutil.copy2(narrated, dest)
        logger.info("narration #%s promoted -> %s", narration_id, dest)
        return True

    def delete(self, narration_id: int) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute("DELETE FROM tk_video_narrations WHERE id=?", (narration_id,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # DB writers
    # ------------------------------------------------------------------

    def _insert(self, video_id: int, gemini_model: str, voice_id: str,
                tts_model: str, dur: float) -> int:
        n = _now()
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO tk_video_narrations
                    (video_id, status, gemini_model, voice_id, tts_model,
                     duration_s, created_at, updated_at)
                   VALUES (?, 'generating', ?, ?, ?, ?, ?, ?)""",
                (video_id, gemini_model, voice_id, tts_model, dur, n, n),
            )
            conn.commit()
            return cur.lastrowid

    def _finish(self, nid: int, **fields: Any) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with _open_db(self.db_path) as conn:
            conn.execute(
                f"UPDATE tk_video_narrations SET {cols} WHERE id=?",
                (*fields.values(), nid),
            )
            conn.commit()


_svc: Optional[VideoNarrationService] = None


def get_video_narration_service() -> VideoNarrationService:
    global _svc
    if _svc is None:
        _svc = VideoNarrationService()
    return _svc
