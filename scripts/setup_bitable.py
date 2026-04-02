"""一键搭建飞书多维表格 — 贴纸选题看板

自动完成:
  1. 创建多维表格 (或使用已有的)
  2. 创建三张数据表 + 全部字段
  3. 创建常用视图
  4. 输出环境变量配置

用法:
  # 新建多维表格
  python -m scripts.setup_bitable

  # 在已有多维表格中建表
  python -m scripts.setup_bitable --app-token XxxBxxxxx

前提:
  .env 中已配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET
"""

from __future__ import annotations

import io
import os
import sys
import time
import argparse
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

FEISHU_HOST = "https://open.feishu.cn"

# ── 字段类型常量 ──────────────────────────────────────────
TEXT = 1
NUMBER = 2
SINGLE_SELECT = 3
MULTI_SELECT = 4
DATE = 5
CHECKBOX = 7
URL = 15
CREATED_TIME = 1001
MODIFIED_TIME = 1002


# ══════════════════════════════════════════════════════════
# 表定义
# ══════════════════════════════════════════════════════════

NEWS_TABLE = {
    "name": "热点资讯榜",
    "default_view_name": "全部数据",
    "fields": [
        {"field_name": "话题名称", "type": TEXT},
        {"field_name": "话题类型", "type": TEXT},
        {"field_name": "机会评分", "type": NUMBER,
         "property": {"formatter": "0"}},
        {"field_name": "热度分", "type": NUMBER,
         "property": {"formatter": "0"}},
        {"field_name": "适配等级", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "HIGH", "color": 1},
             {"name": "MEDIUM", "color": 3},
             {"name": "LOW", "color": 7},
         ]}},
        {"field_name": "包型推荐", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "reaction_pack"},
             {"name": "fan_pack"},
             {"name": "aesthetic_pack"},
             {"name": "meme_pack"},
             {"name": "seasonal_pack"},
             {"name": "identity_pack"},
             {"name": "greeting_pack"},
         ]}},
        {"field_name": "销售平台", "type": TEXT},
        {"field_name": "简要说明", "type": TEXT},
        {"field_name": "情感核心", "type": TEXT},
        {"field_name": "视觉元素", "type": TEXT},
        {"field_name": "风险标记", "type": TEXT},
        {"field_name": "决策", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "recommend", "color": 1},
             {"name": "review", "color": 3},
         ]}},
        {"field_name": "审核状态", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "待审核", "color": 3},
             {"name": "已采纳", "color": 1},
             {"name": "已跳过", "color": 7},
         ]}},
        {"field_name": "操作", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "-"},
             {"name": "待生成", "color": 3},
             {"name": "生成中", "color": 5},
             {"name": "已完成", "color": 1},
             {"name": "失败", "color": 54},
         ]}},
        {"field_name": "日期", "type": DATE,
         "property": {"date_formatter": "yyyy-MM-dd"}},
    ],
    "views": [
        {"view_name": "今日推荐", "view_type": "grid"},
        {"view_name": "待审核", "view_type": "grid"},
        {"view_name": "已采纳", "view_type": "grid"},
        {"view_name": "生成队列", "view_type": "grid"},
    ],
}

TK_TABLE = {
    "name": "TK话题热点榜",
    "default_view_name": "全部数据",
    "fields": [
        {"field_name": "话题标签", "type": TEXT},
        {"field_name": "播放量", "type": NUMBER,
         "property": {"formatter": "0"}},
        {"field_name": "发布数", "type": NUMBER,
         "property": {"formatter": "0"}},
        {"field_name": "AI审核决策", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "approve", "color": 1},
             {"name": "watchlist", "color": 3},
             {"name": "reject", "color": 54},
         ]}},
        {"field_name": "机会评分", "type": NUMBER,
         "property": {"formatter": "0"}},
        {"field_name": "适配等级", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "HIGH", "color": 1},
             {"name": "MEDIUM", "color": 3},
             {"name": "LOW", "color": 7},
         ]}},
        {"field_name": "包型推荐", "type": TEXT},
        {"field_name": "简要说明", "type": TEXT},
        {"field_name": "审核状态", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "待审核", "color": 3},
             {"name": "已采纳", "color": 1},
             {"name": "已跳过", "color": 7},
         ]}},
        {"field_name": "操作", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "-"},
             {"name": "待生成", "color": 3},
             {"name": "生成中", "color": 5},
             {"name": "已完成", "color": 1},
             {"name": "失败", "color": 54},
         ]}},
        {"field_name": "日期", "type": DATE,
         "property": {"date_formatter": "yyyy-MM-dd"}},
    ],
    "views": [
        {"view_name": "今日话题", "view_type": "grid"},
        {"view_name": "待审核", "view_type": "grid"},
        {"view_name": "已采纳", "view_type": "grid"},
        {"view_name": "生成队列", "view_type": "grid"},
    ],
}

HISTORY_TABLE = {
    "name": "生成记录",
    "default_view_name": "全部记录",
    "fields": [
        {"field_name": "话题名称", "type": TEXT},
        {"field_name": "来源", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "news"},
             {"name": "tiktok"},
         ]}},
        {"field_name": "贴纸数量", "type": NUMBER,
         "property": {"formatter": "0"}},
        {"field_name": "耗时(秒)", "type": NUMBER,
         "property": {"formatter": "0.0"}},
        {"field_name": "输出目录", "type": TEXT},
        {"field_name": "状态", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "运行中", "color": 5},
             {"name": "已完成", "color": 1},
             {"name": "失败", "color": 54},
         ]}},
        {"field_name": "Listing状态", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "待处理", "color": 3},
             {"name": "已完成", "color": 1},
         ]}},
        {"field_name": "Blog状态", "type": SINGLE_SELECT,
         "property": {"options": [
             {"name": "待处理", "color": 3},
             {"name": "已完成", "color": 1},
         ]}},
        {"field_name": "创建时间", "type": TEXT},
        {"field_name": "更新时间", "type": TEXT},
        {"field_name": "错误信息", "type": TEXT},
    ],
    "views": [
        {"view_name": "运行中", "view_type": "grid"},
        {"view_name": "已完成", "view_type": "grid"},
        {"view_name": "失败", "view_type": "grid"},
    ],
}


# ══════════════════════════════════════════════════════════
# API 封装
# ══════════════════════════════════════════════════════════

class FeishuAPI:
    """轻量 Feishu REST API 封装，仅用于搭建。"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = ""
        self._token_expires = 0.0

    @property
    def token(self) -> str:
        if time.time() >= self._token_expires:
            self._refresh_token()
        return self._token

    def _refresh_token(self) -> None:
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"获取 tenant_access_token 失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200) - 60

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def create_bitable(self, name: str, folder_token: str = "") -> dict:
        """创建多维表格，返回 {app_token, url}。"""
        body: dict = {"name": name}
        if folder_token:
            body["folder_token"] = folder_token

        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps",
            headers=self._headers(),
            json=body,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建多维表格失败: {data}")

        app = data["data"]["app"]
        return {
            "app_token": app["app_token"],
            "url": app.get("url", ""),
        }

    def create_table(
        self,
        app_token: str,
        table_def: dict,
    ) -> str:
        """创建数据表 + 字段，返回 table_id。"""
        body = {
            "table": {
                "name": table_def["name"],
                "default_view_name": table_def.get(
                    "default_view_name", "全部数据"),
                "fields": table_def["fields"],
            }
        }

        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{app_token}/tables",
            headers=self._headers(),
            json=body,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"创建表 {table_def['name']} 失败: {data}")

        return data["data"]["table_id"]

    def create_view(
        self,
        app_token: str,
        table_id: str,
        view_name: str,
        view_type: str = "grid",
    ) -> str:
        """创建视图，返回 view_id。"""
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/views",
            headers=self._headers(),
            json={"view_name": view_name, "view_type": view_type},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"    ⚠ 创建视图 '{view_name}' 失败: "
                  f"{data.get('msg', '')}")
            return ""
        return data["data"]["view"]["view_id"]

    def transfer_owner(
        self,
        app_token: str,
        owner_type: str,
        owner_id: str,
    ) -> bool:
        """将多维表格的所有者转移给指定用户。

        需要应用开启权限: docs:doc:owner:transfer
        owner_type: "open_id" | "user_id" | "union_id"
        """
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/drive/v1/permissions"
            f"/{app_token}/members/transfer_owner"
            f"?type=bitable&need_notification=true",
            headers=self._headers(),
            json={
                "member_type": owner_type,
                "member_id": owner_id,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"  ⚠ 转移所有者失败: {data.get('msg', data)}")
            return False
        print(f"  ✅ 已将所有权转移给 {owner_type}={owner_id}")
        return True

    def add_collaborator(
        self,
        app_token: str,
        member_type: str,
        member_id: str,
        perm: str = "full_access",
    ) -> bool:
        """给多维表格添加协作者。

        需要应用开启权限: drive:drive:permission:member
        member_type: "open_id" | "user_id" | "union_id" | "email"
        perm: "view" | "edit" | "full_access"
        """
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/drive/v1/permissions"
            f"/{app_token}/members"
            f"?type=bitable&need_notification=true",
            headers=self._headers(),
            json={
                "member_type": member_type,
                "member_id": member_id,
                "perm": perm,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"  ⚠ 添加协作者失败: {data.get('msg', data)}")
            return False
        print(f"  ✅ 已添加协作者 {member_type}={member_id} (权限={perm})")
        return True

    def set_link_sharing(
        self,
        app_token: str,
        link_share_entity: str = "tenant_editable",
    ) -> bool:
        """设置多维表格的链接分享权限。

        需要应用开启权限: drive:drive:permission:public
        link_share_entity 可选值:
          - "tenant_readable"  — 组织内获得链接的人可阅读
          - "tenant_editable"  — 组织内获得链接的人可编辑
          - "anyone_readable"  — 互联网上获得链接的人可阅读
          - "anyone_editable"  — 互联网上获得链接的人可编辑
        """
        resp = requests.patch(
            f"{FEISHU_HOST}/open-apis/drive/v1/permissions"
            f"/{app_token}/public?type=bitable",
            headers=self._headers(),
            json={
                "external_access_entity": "open",
                "security_entity": "anyone_can_view",
                "comment_entity": "anyone_can_view",
                "share_entity": "anyone",
                "link_share_entity": link_share_entity,
                "invite_external": True,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"  ⚠ 设置链接分享失败: {data.get('msg', data)}")
            return False

        labels = {
            "tenant_readable": "组织内获得链接的人可阅读",
            "tenant_editable": "组织内获得链接的人可编辑",
            "anyone_readable": "互联网获得链接的人可阅读",
            "anyone_editable": "互联网获得链接的人可编辑",
        }
        print(f"  ✅ 链接分享已开启: "
              f"{labels.get(link_share_entity, link_share_entity)}")
        return True

    def delete_default_table(self, app_token: str) -> None:
        """删除新建多维表格时自动生成的默认空表。"""
        resp = requests.get(
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{app_token}/tables",
            headers=self._headers(),
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            return

        tables = data.get("data", {}).get("items", [])
        for t in tables:
            if t.get("name") in ("数据表", "Table1", "表格"):
                tid = t["table_id"]
                requests.delete(
                    f"{FEISHU_HOST}/open-apis/bitable/v1/apps/"
                    f"{app_token}/tables/{tid}",
                    headers=self._headers(),
                    timeout=10,
                )
                print(f"  🗑 已删除默认空表 '{t['name']}'")


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="一键搭建飞书多维表格 - 贴纸选题看板",
    )
    parser.add_argument(
        "--app-token", default="",
        help="已有多维表格的 app_token (不传则新建)",
    )
    parser.add_argument(
        "--folder-token", default="",
        help="新建多维表格时放入指定文件夹 (可选)",
    )
    parser.add_argument(
        "--grant-email", default="",
        help="授权指定邮箱为协作者 (full_access 权限)",
    )
    parser.add_argument(
        "--grant-user-id", default="",
        help="授权指定 user_id 为协作者 (full_access 权限)",
    )
    parser.add_argument(
        "--grant-open-id", default="",
        help="授权指定 open_id 为协作者 (给机器人发消息可获取)",
    )
    parser.add_argument(
        "--transfer-owner-email", default="",
        help="将多维表格所有权转移给指定邮箱 (需要 transfer 权限)",
    )
    parser.add_argument(
        "--link-share", default="",
        choices=["tenant_readable", "tenant_editable",
                 "anyone_readable", "anyone_editable"],
        help="开启链接分享 (无需知道用户ID，最简单)",
    )
    parser.add_argument(
        "--permissions-only",
        action="store_true",
        help="仅执行协作者/链接分享，不创建表。"
             " app_token 取自 --app-token 或 FEISHU_BITABLE_APP_TOKEN",
    )
    args = parser.parse_args()

    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        print("❌ 请先在 .env 中配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        sys.exit(1)

    api = FeishuAPI(app_id, app_secret)

    print()
    print("═" * 60)
    print("  飞书多维表格 — 贴纸选题看板 搭建工具")
    print("═" * 60)

    # ── 仅权限模式: 不建表 ──
    if args.permissions_only:
        app_token = (
            args.app_token or os.getenv("FEISHU_BITABLE_APP_TOKEN", ""))
        if not app_token:
            print("❌ --permissions-only 需要 --app-token 或 "
                  "环境变量 FEISHU_BITABLE_APP_TOKEN")
            sys.exit(1)
        print(f"\n  ✓ 权限设置目标: {app_token}")
        if args.grant_email:
            print(f"\n  🔑 授权协作者: {args.grant_email}")
            api.add_collaborator(app_token, "email", args.grant_email)
        if args.grant_user_id:
            print(f"\n  🔑 授权协作者: {args.grant_user_id}")
            api.add_collaborator(app_token, "user_id", args.grant_user_id)
        if args.grant_open_id:
            print(f"\n  🔑 授权协作者: {args.grant_open_id}")
            api.add_collaborator(app_token, "open_id", args.grant_open_id)
        if args.transfer_owner_email:
            print(f"\n  👑 转移所有权: {args.transfer_owner_email}")
            api.transfer_owner(app_token, "email", args.transfer_owner_email)
        if args.link_share:
            print(f"\n  🔗 设置链接分享...")
            api.set_link_sharing(app_token, args.link_share)
        elif not any([
            args.grant_email, args.grant_user_id, args.grant_open_id,
            args.transfer_owner_email,
        ]):
            print("❌ --permissions-only 请至少指定一项: "
                  "--link-share / --grant-* / --transfer-owner-email")
            sys.exit(1)
        print("\n  ✅ 权限操作完成")
        return

    # ── Step 1: 多维表格 ──
    app_token = args.app_token
    bitable_url = ""

    if app_token:
        print(f"\n  ✓ 使用已有多维表格: {app_token}")
    else:
        print("\n  📋 创建多维表格...")
        result = api.create_bitable(
            "贴纸选题看板", args.folder_token)
        app_token = result["app_token"]
        bitable_url = result["url"]
        print(f"  ✅ 已创建: {app_token}")
        if bitable_url:
            print(f"  🔗 {bitable_url}")

    # ── Step 2: 创建三张表 ──
    table_ids: dict[str, str] = {}

    for key, table_def in [
        ("news", NEWS_TABLE),
        ("tk", TK_TABLE),
        ("history", HISTORY_TABLE),
    ]:
        name = table_def["name"]
        print(f"\n  📊 创建表: {name}...")

        try:
            table_id = api.create_table(app_token, table_def)
            table_ids[key] = table_id
            field_count = len(table_def["fields"])
            print(f"  ✅ {name} ({field_count} 个字段) → {table_id}")
        except RuntimeError as e:
            print(f"  ❌ {e}")
            continue

        # 创建视图
        for view_def in table_def.get("views", []):
            view_id = api.create_view(
                app_token,
                table_id,
                view_def["view_name"],
                view_def.get("view_type", "grid"),
            )
            if view_id:
                print(f"    📌 视图: {view_def['view_name']}")

    # ── Step 3: 清理默认空表 ──
    if not args.app_token:
        api.delete_default_table(app_token)

    # ── Step 3.5: 授权协作者 / 转移所有者 ──
    if args.grant_email:
        print(f"\n  🔑 授权协作者: {args.grant_email}")
        api.add_collaborator(app_token, "email", args.grant_email)

    if args.grant_user_id:
        print(f"\n  🔑 授权协作者: {args.grant_user_id}")
        api.add_collaborator(app_token, "user_id", args.grant_user_id)

    if args.grant_open_id:
        print(f"\n  🔑 授权协作者: {args.grant_open_id}")
        api.add_collaborator(app_token, "open_id", args.grant_open_id)

    if args.transfer_owner_email:
        print(f"\n  👑 转移所有权: {args.transfer_owner_email}")
        api.transfer_owner(app_token, "email", args.transfer_owner_email)

    if args.link_share:
        print(f"\n  🔗 设置链接分享...")
        api.set_link_sharing(app_token, args.link_share)

    # ── Step 4: 输出配置 ──
    print("\n" + "═" * 60)
    print("  ✅ 搭建完成！")
    print("═" * 60)

    env_lines = [
        "",
        "# ── 飞书多维表格 (贴纸选题看板) ──",
        f"FEISHU_BITABLE_APP_TOKEN={app_token}",
    ]
    if table_ids.get("news"):
        env_lines.append(f"FEISHU_TABLE_NEWS={table_ids['news']}")
    if table_ids.get("tk"):
        env_lines.append(f"FEISHU_TABLE_TK={table_ids['tk']}")
    if table_ids.get("history"):
        env_lines.append(f"FEISHU_TABLE_HISTORY={table_ids['history']}")
    if bitable_url:
        env_lines.append(f"FEISHU_BITABLE_URL={bitable_url}")

    print("\n  请将以下配置添加到 .env 文件:\n")
    print("  ┌─────────────────────────────────────────────")
    for line in env_lines:
        print(f"  │ {line}")
    print("  └─────────────────────────────────────────────")

    # 尝试自动追加到 .env
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        try:
            existing = env_path.read_text(encoding="utf-8")
            if "FEISHU_BITABLE_APP_TOKEN" not in existing:
                with open(env_path, "a", encoding="utf-8") as f:
                    f.write("\n" + "\n".join(env_lines) + "\n")
                print(f"\n  ✅ 已自动追加到 {env_path}")
            else:
                print(f"\n  ⚠ .env 中已有 FEISHU_BITABLE_APP_TOKEN，"
                      f"请手动更新")
        except Exception as e:
            print(f"\n  ⚠ 无法写入 .env: {e}，请手动添加")

    print("\n  后续步骤:")
    print("  1. 打开多维表格，点击左上角「切换为应用模式」")
    print("  2. 拖拽搭建页面 (仪表盘、列表页、详情页)")
    print("  3. 运行 python -m scripts.bitable_sync push 同步数据")
    print()


if __name__ == "__main__":
    main()
