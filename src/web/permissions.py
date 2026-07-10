"""按角色的功能开关 —— 控制各平台上架入口对不同用户的显隐。

设计:
- ``ROLE_PLATFORMS``: 角色 → 可见的平台上架入口集合。
- 用户 → 角色: 从环境变量 ``FEISHU_ROLES_JSON`` 读(一个 {标识: 角色} 的 JSON,
  标识可用飞书 open_id / en_name / name 任一)。**未配置的用户默认 admin**
  (看全部, 避免新人/本地开发被误锁在外)。
- 模板门控: 注入为 Jinja 全局 ``can_see``, 用法 ``{% if can_see(request, 'etsy') %}``。

登录用户身份来自 ``request.session["user"]`` (见 auth_middleware.py)。
改角色映射只需改 .env 的 FEISHU_ROLES_JSON, 无需动代码。
"""

from __future__ import annotations

import json
import os
from typing import Any

# 角色 → 可见的平台上架入口
ROLE_PLATFORMS: dict[str, set[str]] = {
    "admin": {"etsy", "shopify", "tiktok", "amazon"},   # 管理员: 全部平台
    "operator": {"etsy", "shopify", "tiktok"},          # 运营: 上架三平台(不含已下线的 Amazon)
    "cs": set(),                                         # 客服: 不显示任何上架入口
}
DEFAULT_ROLE = "admin"
ALL_PLATFORMS = {"etsy", "shopify", "tiktok", "amazon"}


def _load_user_roles() -> dict[str, str]:
    """{open_id/en_name/name: role} —— 从 FEISHU_ROLES_JSON 读, 坏 JSON 返回空。"""
    raw = (os.getenv("FEISHU_ROLES_JSON") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def role_for_user(user: dict[str, Any] | None) -> str:
    """登录用户 → 角色; 未命中映射的默认 DEFAULT_ROLE(admin)。"""
    user = user or {}
    roles = _load_user_roles()
    for key in (user.get("open_id"), user.get("en_name"), user.get("name")):
        if key and str(key) in roles:
            return roles[str(key)]
    return DEFAULT_ROLE


def visible_platforms(request) -> set[str]:
    """当前登录用户可见的平台集合。"""
    try:
        user = request.session.get("user")
    except Exception:  # noqa: BLE001 — session 不可用时按默认角色
        user = None
    role = role_for_user(user)
    return ROLE_PLATFORMS.get(role, ROLE_PLATFORMS[DEFAULT_ROLE])


def can_see(request, platform: str) -> bool:
    """模板门控: 当前登录用户能否看到某平台的上架入口。"""
    return platform in visible_platforms(request)
