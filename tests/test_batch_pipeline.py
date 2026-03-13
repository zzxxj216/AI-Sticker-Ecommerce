"""批量生成管线测试

测试 BatchSession 对话驱动流程：
  输入主题 → 规划 6 包 → 话题 + 概念 → style guide + ideas → 话题预览 → 编辑

用法:
    python -m tests.test_batch_pipeline
    或
    pytest tests/test_batch_pipeline.py -s
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.batch.batch_session import BatchSession, BatchSessionResponse


def print_response(resp: BatchSessionResponse) -> None:
    """打印会话响应。"""
    print(f"\n{'='*60}")
    print(f"Phase: {resp.phase}")
    print(f"Message:\n{resp.message}")
    if resp.data:
        print(f"Data: {json.dumps(resp.data, ensure_ascii=False, indent=2, default=str)}")
    print(f"{'='*60}\n")


def progress_printer(event: str, data: dict) -> None:
    """进度回调：实时打印每步结果。"""
    print(f"\n  >> [{event}] {json.dumps(data, ensure_ascii=False, default=str)[:200]}")


def test_batch_session_interactive():
    """交互式测试：通过终端对话驱动批量生成。"""
    session = BatchSession()

    print("=" * 60)
    print("AI 贴纸批量生成 - 对话模式")
    print("输入主题和偏好，系统将并行生成 6 个贴纸包")
    print("输入 /reset 重置, /quit 退出")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not user_input:
            continue

        if user_input.lower() == "/quit":
            print("再见！")
            break

        if user_input.lower() == "/reset":
            session.reset()
            print("会话已重置。")
            continue

        if user_input.lower() == "/results":
            print(session.print_step_results())
            continue

        resp = session.process_message(user_input)
        print_response(resp)

        if resp.data.get("ready"):
            pack_count = resp.data.get("pack_count", 6)
            print(f"\n>>> 开始批量生成管线 ({pack_count} 包, skip_images=True 仅生成预览) <<<\n")
            try:
                result = session.start_pipeline(
                    target_per_pack=55,
                    stickers_per_topic=8,
                    skip_images=True,
                    on_progress=progress_printer,
                )
                print("\n>>> 管线完成！<<<")
                print(json.dumps(result.to_summary(), ensure_ascii=False, indent=2, default=str))
                print("\n现在进入编辑模式。")
                print("输入如：「卡包1话题XXX的第3张改成更温馨」")
                print("输入 /previews 查看预览图")
                print("输入 /summary 查看摘要")
                print("输入 /done 完成")
            except Exception as e:
                print(f"\n>>> 管线执行失败: {e}")
                import traceback
                traceback.print_exc()


def test_batch_pipeline_unit():
    """单元测试：验证 BatchSession 对话收集逻辑。"""
    session = BatchSession()

    resp1 = session.process_message("我想做一个关于程序员日常的贴纸包")
    assert resp1.phase in ("batch_input", "batch_planning")
    assert resp1.message
    print(f"[Test 1] Phase: {resp1.phase}, Theme detected: {resp1.data.get('theme')}")

    resp2 = session.process_message("风格用赛博朋克，色彩以蓝紫为主")
    assert resp2.message
    print(f"[Test 2] Phase: {resp2.phase}, Style: {resp2.data.get('style')}")

    print("[Unit tests passed]")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--unit":
        test_batch_pipeline_unit()
    else:
        test_batch_session_interactive()
