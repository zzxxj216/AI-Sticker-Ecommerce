#!/usr/bin/env python3
"""
最小版 split POC：

读取一张已经存在的 preview 图，
调用 AiHubMix 的图片编辑接口，
拆出其中 1 张贴纸。

运行：

    python scripts/poc_w1_image_split.py
    python scripts/poc_w1_image_split.py --sticker-number 3
    python scripts/poc_w1_image_split.py --image output/poc_w1/preview.png
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

OUT_DIR = ROOT_DIR / "output" / "poc_w1"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_aihubmix_client() -> OpenAI:
    """
    创建 AiHubMix 的 OpenAI 兼容客户端。

    .env 里现在是：
    AIHUBMIX_BASE_URL=https://aihubmix.com/v1/chat/completions

    但 OpenAI SDK 需要的是 /v1，而不是 /v1/chat/completions，
    所以这里要裁掉后半段。
    """
    api_key = os.getenv("AIHUBMIX_API_KEY", "").strip()
    base_url = os.getenv("AIHUBMIX_BASE_URL", "https://aihubmix.com/v1").strip()

    if not api_key:
        raise ValueError("没有找到 AIHUBMIX_API_KEY，请检查 .env 文件。")

    if base_url.rstrip("/").endswith("chat/completions"):
        base_url = base_url.rstrip("/").rsplit("/", 2)[0]

    return OpenAI(api_key=api_key, base_url=base_url, timeout=600)


def decode_image_result(item) -> bytes:
    """把接口返回的单张图片解析成 bytes。"""
    if getattr(item, "b64_json", None):
        return base64.b64decode(item.b64_json)

    if getattr(item, "url", None):
        return requests.get(item.url, timeout=120).content

    raise ValueError("接口返回里没有图片内容（既没有 b64_json，也没有 url）。")


def build_split_prompt(sticker_number: int,sticker_end:int) -> str:
    """
    构造拆图提示词。

    sticker_number 按“从左到右、从上到下”计数。
    """
    return f"""
Extract sticker number {sticker_number} to {sticker_end}from this sticker sheet
(counting left-to-right, top-to-bottom).

Return only that one sticker.
Place it in the center of a clean white background.
Keep the original text, colors, and die-cut border.
Do not show any other stickers.
""".strip()


def split_one_sticker(
    *,
    image_path: Path,
    sticker_number: int,
    model: str,
) -> bytes:
    """调用 AiHubMix 的 images.edit，把 1 张预览图拆成 1 张贴纸。"""
    if not image_path.is_file():
        raise FileNotFoundError(f"找不到输入图片：{image_path}")

    client = get_aihubmix_client()
    prompt = build_split_prompt(1,5)

    with image_path.open("rb") as image_file:
        response = client.images.edit(
            model=model,
            image=image_file,
            prompt=prompt,
            n=5,
            size="1024x1024",
        )
        raise ValueError("接口调用成功，但 response.data 是空的。")
    image_all = []
    for data in response.data:
        image_all.append(decode_image_result(data))
    return image_all


def main() -> None:
    parser = argparse.ArgumentParser(description="使用 AiHubMix 拆出单张贴纸")
    parser.add_argument(
        "--image",
        default=str(OUT_DIR / "preview.png"),
        help="输入预览图路径，默认是 output/poc_w1/preview.png",
    )
    parser.add_argument(
        "--sticker-number",
        type=int,
        default=3,
        help="要拆第几张贴纸，按从左到右、从上到下计数，默认 1",
    )
    parser.add_argument(
        "--model",
        default="gpt-image-2",
        help="AiHubMix 上使用的图片模型，默认 gpt-image-1",
    )
    args = parser.parse_args()

    image_path = Path(args.image)


    print(f"输入图片：{image_path}")
    print(f"目标贴纸序号：{args.sticker_number}")
    print(f"模型：{args.model}")
    print("开始拆分...")

    result_bytes = split_one_sticker(
        image_path=image_path,
        sticker_number=args.sticker_number,
        model=args.model,
    )
    for i,data in enumerate(result_bytes):

        output_path = OUT_DIR / f"split_{args.sticker_number}_{i}.png"
        output_path.write_bytes(result_bytes)

    print("完成。")
    print(f"输出图片：{output_path}")


if __name__ == "__main__":
    main()
