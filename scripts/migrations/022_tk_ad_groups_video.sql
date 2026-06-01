-- 多视频投放：每个广告组记住自己投的视频 + 身份（Phase 4）.
--
-- 背景：原本一个实验只投一条视频（tk_ad_experiments.promote_ref_id 单值），
-- 广告组只按人群区分。现在支持「N 条视频 × M 个人群 = N×M 个广告组」——每个
-- 广告组绑定自己那条视频，所以视频 id 必须下沉到广告组级。
--
-- tk_ad_experiments.promote_ref_id 保留（首条/兼容用）；真实每组投哪条看
-- tk_ad_groups.promote_ref_id。identity_id 同理下沉到组级（Spark 身份）。

ALTER TABLE tk_ad_groups ADD COLUMN promote_ref_id TEXT NOT NULL DEFAULT '';
ALTER TABLE tk_ad_groups ADD COLUMN identity_id    TEXT NOT NULL DEFAULT '';
