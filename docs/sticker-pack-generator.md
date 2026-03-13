# 贴纸包自动生成器

## 功能介绍

根据科技主题自动生成 30-50 张精美贴纸，包含三种类型：

1. **📝 纯文本贴纸**: 简短有力的文字（口号、梗、流行语）
2. **🎨 元素贴纸**: 主题相关的视觉元素（图标、符号、卡通形象）
3. **🔀 组合贴纸**: 文字与元素的完美结合

## 技术架构

- **创意生成**: Claude API（高温度采样，确保创意多样性）
- **图片生成**: Gemini 3.1 Flash Image Preview（快速、高质量）
- **并发处理**: 多线程并发生成，提升效率

## 快速开始

### 1. 环境配置

确保 `.env` 文件中已配置：

```bash
# Claude API（用于创意生成）
ANTHROPIC_API_KEY=sk-claude-xxx
ANTHROPIC_BASE_URL=https://esapi.top

# Gemini API（用于图片生成）
IMAGE_API_KEY=AIzaSyB1DWP9g1DAiIwFuKz3c_74voTRLskl4BM
IMAGE_BASE_URL=https://generativelanguage.googleapis.com
IMAGE_MODEL=gemini-3.1-flash-image-preview
```

### 2. 安装依赖

```bash
pip install anthropic gradio
```

### 3. 使用方式

#### 方式一：命令行（交互式）

```bash
cd trend_fetcher
python sticker_pack_cli.py
```

然后按提示输入主题和数量。

#### 方式二：命令行（快速生成）

```bash
python sticker_pack_cli.py --theme "AI人工智能" --count 40
```

自定义类型占比：

```bash
python sticker_pack_cli.py --theme "区块链" --count 50 \
  --text-ratio 0.4 --element-ratio 0.3 --hybrid-ratio 0.3
```

#### 方式三：Web UI（推荐）

```bash
python sticker_pack_webui.py
```

然后在浏览器打开 `http://localhost:7860`

创建公共分享链接：

```bash
python sticker_pack_webui.py --share
```

#### 方式四：Python 代码调用

```python
from agents.sticker_pack_generator import StickerPackGenerator

generator = StickerPackGenerator()

result = generator.generate_pack(
    theme="AI人工智能",
    total_count=40,
    text_ratio=0.3,      # 30% 纯文本
    element_ratio=0.35,  # 35% 元素
    hybrid_ratio=0.35    # 35% 组合
)

print(f"成功生成 {result['success_count']} 张贴纸")
print(f"结果文件: {result['result_file']}")
```

## 输出结果

### 文件结构

```
output/
├── sticker_packs/
│   └── pack_AI人工智能_20260303_143022.json  # 生成结果（JSON）
└── images/
    └── 20260303/
        ├── material_01_ai_power_143025.png
        ├── material_02_robot_icon_143028.png
        └── ...
```

### JSON 结果格式

```json
{
  "theme": "AI人工智能",
  "timestamp": "20260303_143022",
  "total_count": 40,
  "success_count": 38,
  "elapsed": 125.6,
  "ideas": [
    {
      "index": 1,
      "type": "text",
      "title": "AI赋能",
      "text_content": "AI赋能",
      "image_prompt": "Bold white text 'AI赋能' on vibrant gradient...",
      "success": true,
      "image_path": "F:/练习模块/AI-Sticker-Ecommerce/trend_fetcher/output/images/20260303/material_01_ai_power_143025.png",
      "filename": "material_01_ai_power_143025.png",
      "size_kb": 156,
      "elapsed": 3.2
    }
  ],
  "statistics": {
    "total": 40,
    "success": 38,
    "failed": 2,
    "by_type": {
      "text": 12,
      "element": 14,
      "hybrid": 14
    },
    "total_size_kb": 5824,
    "avg_generation_time": 3.14
  }
}
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `theme` | str | 必填 | 科技主题（如：AI人工智能、区块链） |
| `total_count` | int | 40 | 总贴纸数量（10-100） |
| `text_ratio` | float | 0.3 | 纯文本贴纸占比 |
| `element_ratio` | float | 0.35 | 元素贴纸占比 |
| `hybrid_ratio` | float | 0.35 | 组合贴纸占比 |

**注意**: 三种类型占比之和必须为 1.0

## 主题示例

- AI人工智能
- 区块链
- 元宇宙
- 量子计算
- 云计算
- 5G通信
- 物联网
- 大数据
- 机器学习
- 自动驾驶

## 性能指标

- **创意生成**: ~10-20秒（Claude API）
- **图片生成**: ~3-5秒/张（Gemini API，并发3线程）
- **总耗时**: 40张约 2-3 分钟

## 常见问题

### Q: 生成失败怎么办？

A: 检查以下几点：
1. API Key 是否正确配置
2. 网络连接是否正常
3. API 配额是否充足
4. 查看错误日志定位问题

### Q: 如何提高生成速度？

A: 可以调整并发数（默认3）：

```python
# 在 sticker_pack_generator.py 中修改
image_results = image_gen.generate_batch(
    sticker_ideas=ideas,
    max_workers=5  # 增加并发数（注意 API 限流）
)
```

### Q: 如何自定义贴纸风格？

A: 修改 `sticker_pack_generator.py` 中的 `_build_creative_prompt` 方法，调整提示词中的风格描述。

### Q: 生成的图片在哪里？

A: 默认保存在 `output/images/YYYYMMDD/` 目录下，按日期分类。

## 高级用法

### 批量生成多个主题

```python
themes = ["AI人工智能", "区块链", "元宇宙", "量子计算"]
generator = StickerPackGenerator()

for theme in themes:
    print(f"\n生成主题: {theme}")
    result = generator.generate_pack(theme=theme, total_count=30)
    print(f"完成: {result['success_count']}/{result['total_count']}")
```

### 自定义输出目录

修改 `.env` 文件：

```bash
OUTPUT_DIR=./custom_output
```

### 使用参考图

在创意生成后，可以为特定贴纸添加参考图：

```python
ideas = [
    {
        "index": 1,
        "type": "element",
        "title": "机器人",
        "image_prompt": "Cute robot character...",
        "reference_image": "/path/to/reference.png"  # 添加参考图
    }
]

image_gen = StickerImageGenerator()
results = image_gen.generate_batch(ideas)
```

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License
