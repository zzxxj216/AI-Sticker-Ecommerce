# 贴纸风格分析与变种生成器

## 🎯 功能介绍

上传一张贴纸图片，AI 将：
1. **📊 分析风格特征** - 识别视觉风格、色彩方案、设计元素、情感表达等
2. **🎯 生成提示词** - 提取可复用的风格描述和图片生成提示词
3. **🔀 创建变种** - 保持原始风格一致，生成多个变种贴纸

## ✨ 核心特性

- 🤖 **AI 视觉分析** - Claude Vision 深度分析贴纸风格
- 🎨 **多维度分析** - 视觉风格、色彩、元素、情感、技术特点
- 🔄 **智能变种** - 保持风格一致性，生成创意变种
- 📊 **可调变化度** - 微调/适度/大幅三种变化程度
- 🖼️ **参考图生成** - 使用原图作为参考，确保风格一致

## 🚀 快速开始

### 1. 环境配置

确保 `.env` 文件中已配置（已配置好）：

```bash
# Claude API（风格分析）
ANTHROPIC_API_KEY=your-claude-key
ANTHROPIC_BASE_URL=https://esapi.top

# Gemini API（图片生成）
IMAGE_API_KEY=your-gemini-key
IMAGE_BASE_URL=https://generativelanguage.googleapis.com
IMAGE_MODEL=gemini-3.1-flash-image-preview
```

### 2. 使用方式

#### 方式一：Web UI（推荐）

```bash
cd trend_fetcher
python style_analyzer_webui.py
```

访问 `http://localhost:7861`

#### 方式二：命令行（快速）

```bash
cd trend_fetcher
python style_analyzer_cli.py --image sticker.png --variants 5 --degree medium
```

#### 方式三：Python API

```python
from agents.sticker_style_analyzer import StickerStyleAnalyzer

analyzer = StickerStyleAnalyzer()
# 分析风格
analysis = analyzer.analyze_sticker_style(
    image_path="sticker.png",
    analysis_language="zh"
)

# 生成变种
variants = analyzer.generate_variants(
    style_analysis=analysis,
    variant_count=5,
    variation_degree="medium"
)

print(f"成功生成 {variants['success_count']} 个变种")
```

## 📊 分析维度

### 1. 视觉风格
- 扁平化设计
- 立体/3D 风格
- 手绘风格
- 像素艺术
- 渐变风格
- 线条艺术

### 2. 色彩方案
- 主色调识别
- 配色方案分析
- 色彩饱和度
- 色彩对比度

### 3. 设计元素
- 图形元素
- 图标符号
- 装饰元素
- 背景元素

### 4. 文字特征（如有）
- 字体风格
- 文字内容
- 排版方式
- 文字效果

### 5. 情感表达
- 传达的情绪
- 使用场景
- 目标受众

### 6. 技术特点
- 线条粗细
- 阴影效果
- 边缘处理
- 细节层次

### 7. 主题类型
- 科技/AI
- 卡通/可爱
- 商务/专业
- 艺术/创意

## 🎨 变化程度说明

| 程度 | 相似度 | 说明 | 适用场景 |
|------|--------|------|------|
| **small** | 90% | 微调细节，如颜色、装饰元素 | 需要高度一致性的系列贴纸 |
| **medium** | 70% | 适度变化，可改变主体形态但保持风格 | 同主题不同表现的贴纸包 |
| **large** | 50% | 大幅变化，可改变主题但保持整体风格 | 探索风格的多种可能性 |

## 💻 使用示例

### 示例 1：基础分析与生成

```bash
# 分析贴纸并生成 5 个中等变化的变种
python style_analyzer_cli.py --image my_sticker.png --variants 5 --degree medium
```

### 示例 2：微调变种

```bash
# 生成 10 个高度相似的变种
python style_analyzer_cli.py --image my_sticker.png --variants 10 --degree small
```

### 示例 3：大幅变化

```bash
# 生成 8 个风格一致但主题不同的变种
python style_analyzer_cli.py --image my_sticker.png --variants 8 --degree large
```

### 示例 4：交互式模式

```bash
# 启动交互式模式，逐步操作
python style_analyzer_cli.py -i
```

### 示例 5：Python API 详细使用

```python
from agents.sticker_style_analyzer import StickerStyleAnalyzer
from pathlib import Path

analyzer = StickerStyleAnalyzer()

# 1. 分析风格
print("分析贴纸风格...")
analysis_result = analyzer.analyze_sticker_style(
    image_path="sticker.png",
    analysis_language="zh"
)

if analysis_result.get("success"):
    analysis = analysis_result["analysis"]

    print(f"视觉风格: {analysis['visual_style']}")
    print(f"色彩方案: {analysis['color_scheme']['description']}")
  print(f"主题类型: {analysis['theme']}")
    print(f"情感表达: {analysis['emotion']}")

    # 2. 生成变种
    print("\n生成变种贴纸...")
    variant_result = analyzer.generate_variants(
        style_analysis=analysis_result,
        variant_count=5,
        variation_degree="medium"
    )

    if variant_result.get("success"):
        print(f"成功生成 {variant_result['success_count']} 个变种")

        # 3. 查看变种详情
        for variant in variant_result["variants"]:
            if variant.get("success"):
                print(f"\n变种: {variant['title']}")
                print(f"  描述: {variant['variant_description']}")
                print(f"  图片: {variant['image_path']}")
```

## 📁 输出结果

### 文件结构

```
output/
├── style_analysis/
│   ├── analysis_20260304_143022.json    # 风格分析结果
│   └── variants_20260304_143145.json    # 变种生成结果
├── images/
│   └── 20260304/
│       ├── material_01_variant_143150.png
│       ├── material_02_variant_143155.png
│       └── ...
└── temp_uploads/
    └── upload_20260304_143020.png       # 上传的原图
```

### 分析结果格式

```json
{
  "success": true,
  "image_path": "sticker.png",
  "analysis": {
    "visual_style": "扁平化设计",
    "color_scheme": {
      "primary_colors": ["#FF6B6B", "#4ECDC4"],
      "description": "鲜艳的红色和青色对比"
    },
    "design_elements": ["机器人图标", "几何图形"],
    "text_features": {
      "has_text": true,
      "text_content": "AI",
      "font_style": "粗体无衬线字体"
    },
    "emotion": "科技感、未来感",
    "theme": "科技/AI",
    "image_prompt_en": "Flat design robot icon with vibrant colors..."
  },
  "elapsed": 12.5
}
```

### 变种结果格式

```json
{
  "success": true,
  "variant_count": 5,
  "success_count": 5,
  "variation_degree": "medium",
  "variants": [
    {
      "index": 1,
      "title": "变种标题",
      "variant_description": "改变了机器人的姿态",
    "text_content": "AI",
      "image_prompt": "...",
      "success": true,
      "image_path": "output/images/20260304/material_01_variant.png",
      "size_kb": 156
    }
  ],
  "elapsed": 125.6
}
```

## 🎯 使用场景

### 场景 1：贴纸系列化
上传一张成功的贴纸，生成同风格的系列贴纸包

### 场景 2：风格探索
上传参考图，探索该风格的多种表现形式

### 场景 3：快速迭代
基于现有贴纸快速生成变种，节省设计时间

### 场景 4：风格学习
分析优秀贴纸的风格特征，学习设计技巧

### 场景 5：品牌一致性
确保生成的贴纸保持品牌视觉风格一致

## ⚙️ 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `image_path` | str | 必填 | 贴纸图片路径 |
| `variant_count` | int | 5 | 变种数量（1-20） |
| `variation_degree` | str | medium | 变化程度（small/medium/large） |
| `analysis_language` | str | zh | 分析语言（zh/en） |

## 🧪 测试

```bash
cd trend_fetcher
python test_style_analyzer.py
```

## 📈 性能指标

- **风格分析**: 10-15 秒
- **单个变种生成**: 3-5 秒
- **5个变种总耗时**: 约 30-40 秒
- **成功率**: 90-95%

## 💡 使用技巧

1. **选择清晰的原图**: 图片越清晰，分析越准确
2. **合适的变化程度**:
   - 需要高度一致性 → small
   - 平衡一致性和多样性 → medium
   - 探索更多可能性 → large
3. **批量生成**: 一次生成多个变种，从中挑选最佳
4. **迭代优化**: 对满意的变种再次分析和生成

## 🔧 高级用法

### 批量处理多张图片

```python
from agents.sticker_style_analyzer import StickerStyleAnalyzer
from pathlib import Path

analyzer = StickerStyleAnalyzer()
image_dir = Path("my_stickers")

for image_path in image_dir.glob("*.png"):
    print(f"\n处理: {image_path.name}")

    # 分析
    analysis = analyzer.analyze_sticker_style(image_path)

    # 生成变种
    if analysis.get("success"):
        variants = analyzer.generate_variants(
            style_analysis=analysis,
            variant_count=3,
            variation_degree="medium"
        )
        print(f"  生成 {variants['success_count']} 个变种")
```

### 自定义变种策略

```python
# 先生成小变化的变种
small_variants = analyzer.generate_variants(
    style_analysis=analysis,
    variant_count=3,
    variation_degree="small"
)

# 再生成大变化的变种
large_variants = analyzer.generate_variants(
    style_analysis=analysis,
    variant_count=3,
    variation_degree="large"
)
```

## 🐛 故障排除

### 问题 1: 图片加载失败
- 检查图片路径是否正确
- 确保图片格式支持（PNG/JPG/WEBP）
- 检查图片文件是否损坏

### 问题 2: 风格分析失败
- 检查 Claude API 配置
- 确保图片清晰可识别
- 查看错误日志

### 问题 3: 变种生成失败
- 检查 Gemini API 配置
- 降低并发数或变种数量
- 检查网络连接

## 🔗 相关功能

- [贴纸包生成器](./sticker-pack-generator.md) - 从主题生成贴纸包
- [图片生成器](../image_generator.py) - 底层图片生成模块

## 📄 许可证

MIT License

---

**开始使用**: 运行 `python style_analyzer_webui.py` 或查看上述示例代码
