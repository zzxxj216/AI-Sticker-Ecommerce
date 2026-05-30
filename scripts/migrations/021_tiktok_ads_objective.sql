-- TikTok 广告系统 Phase 2 — 目标驱动重构的 schema 变更.
--
-- 背景：初版把"投放目标/推广类型"当次要参数，导致 (1) 投视频时"投的是什么"
-- 不清楚、(2) 目标不准、(3) 指标错配（人群库一律按 ROAS 排，投视频时 ROAS 恒
-- 为 0）。本次把目标提为一等输入，并让指标随目标变。
--
-- 变更：
--   1. tk_ad_experiments 增加 Spark 身份字段（视频走 Spark：用某身份推已发帖子）。
--      objective 已在初版的 `objective` 列里，无需新增。
--   2. tk_ad_metrics_snapshots 增加视频/互动指标列。一次 report 调用即可同时取
--      video+product+engagement 全集（已对真实广告主实测），故全量落库；读取期
--      按 experiment.objective 选主 KPI。raw_json 仍保留全量兜底。

-- Spark 投放需要：用哪个身份(identity) + 推哪条已发帖(promote_ref_id=tiktok_video_id)。
ALTER TABLE tk_ad_experiments ADD COLUMN identity_id TEXT NOT NULL DEFAULT '';
ALTER TABLE tk_ad_experiments ADD COLUMN identity_type TEXT NOT NULL DEFAULT '';

-- 视频/互动指标列（实测合法 metric 的本地落库形态）。命名对应：
--   video_views      <- video_play_actions（播放）
--   video_2s/6s      <- video_watched_2s / video_watched_6s
--   video_p100       <- video_views_p100（完播）
--   avg_video_play   <- average_video_play（人均播放时长，秒）
--   profile_visits   <- profile_visits（主页访问）
--   follows          <- follows（涨粉）
--   engagements      <- engagements（互动）
--   reach/frequency  <- reach / frequency
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN video_views    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN video_2s       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN video_6s       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN video_p100     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN avg_video_play REAL    NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN profile_visits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN follows        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN engagements    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN reach          INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_ad_metrics_snapshots ADD COLUMN frequency      REAL    NOT NULL DEFAULT 0;
