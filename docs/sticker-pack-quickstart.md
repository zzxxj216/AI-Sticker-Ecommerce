# 贴纸包生成器 - 快速开始指南

## 🎯 5分钟快速上手

### 第一步：检查环境

确保已安装 Python 3.8+：

```bash
python --version
```

### 第二步：安装依赖

```bash
cd trend_fetcher
pip install anthropic gradio
```

或安装全部依赖：

```bash
pip install -r requirements.txt
```

### 第三步：配置 API

编辑项目根目录的 `.env` 文件（已存在）：

```bash
# Claude API（创意生成）
ANTHROPIC_API_KEY=sk-claude-28039ca54e6b40179a68
ANTHROPIC_BASE_URL=https://esapi.top

# Gemini API（图片生成）
IMAGE_API_KEY=AIzaSyB1DWP9g1DAiIwFuKz3c_74voTRLskl4BM
IMAGE_BASE_URL=https://generativelanguage.googleapis.com
IMAGE_MODEL=gemini-3.1-flash-image-preview
```

✅ 你的 `.env` 已经配置好了，可以直接使用！

### 第四步：开始生成

#### 方式 1：Web UI（最简单）

```bash
cd trend_fetcher
python sticker_pack_webui.py
```

然后在浏览器打开 `http://localhost:7860`

#### 方式 2：命令行交互式

```bash
cd trend_fetcher
python sticker_pack_cli.py
```

按提示输入主题和数量。

#### 方式 3：一键生成

```bash
cd trend_fetcher
python sticker_pack_cli.py --theme "AI人工智能" --count 40
```

### 第五步：查看结果

生成完成后，查看：

- **JSON 结果**: `output/sticker_packs/pack_*.json`
- **图片文件**: `output/images/YYYYMMDD/*.png`

## 🎨 第一次生成示例

### 使用 Web UI

1. 启动 Web UI：
   ```bash
   python sticker_pack_webui.py
   ```

2. 在浏览器中：
   - 输入主题：`AI人工智能`
   - 设置数量：`40`
   - 保持默认占比
   - 点击"开始生成"

3. 等待 2-3 分钟

4. 查看生成的贴纸画廊

### 使用命令行

```bash
# 进入目录
cd trend_fetcher

# 生成 AI 主题贴纸包
python sticker_pack_cli.py --theme "AI人工智能" --count 40

# 等待完成，查看输出
# ✓ 生成成功！
#   结果文件: output/sticker_packs/pack_AI人工智能_20260303_143022.json
```

## 📊 理解输出结果

### JSON 结果文件

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
      "image_prompt": "Bold white text...",
      "success": true,
      "image_path": "F:/练习模块/.../material_01_ai_power_143025.png",
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
    }
  }
}
```

### 图片文件

所有生成的图片保存在：

```
output/images/20260303/
├── material_01_ai_power_143025.png
├── material_02_robot_icon_143028.png
├── material_03_ai_brain_143031.png
└── ...
```

## 🎯 常用场景

### 场景 1：生成单个主题

```bash
python sticker_pack_cli.py --theme "区块链" --count 40
```

### 场景 2：批量生成多个主题

创建脚本 `batch_generate.py`：

```python
from agents.sticker_pack_generator import StickerPackGenerator

themes = ["AI人工智能", "区块链", "元宇宙", "量子计算"]
generator = StickerPackGenerator()

for theme in themes:
    print(f"\n生成主题: {theme}")
    result = generator.generate_pack(theme=theme, total_count=30)
    print(f"完成: {result['success_count']}/30")
```

运行：

```bash
python batch_generate.py
```

### 场景 3：自定义类型占比

```bash
# 生成更多文本类型的贴纸
python sticker_pack_cli.py --theme "云计算" --count 50 \
  --text-ratio 0.5 --element-ratio 0.25 --hybrid-ratio 0.25
```

### 场景 4：小批量测试

```bash
# 先生成 10 张测试
python sticker_pack_cli.py --theme "测试主题" --count 10
```

## 🔧 常见调整

### 调整并发数（提高速度）

编辑 `agents/sticker_pack_generator.py`：

```python
# 找到这一行（约第 140 行）
image_results = image_gen.generate_batch(
    sticker_ideas=ideas,
    max_workers=5  # 从 3 改为 5（注意 API 限流）
)
```

### 调整超时时间

编辑 `.env`：

```bash
IMAGE_TIMEOUT=180  # 从 120 增加到 180 秒
```

### 更换图片模型

编辑 `.env`：

```bash
# 更快的模型
IMAGE_MODEL=gemini-3.1-flash-image-preview

# 更高质量的模型（更慢）
# IMAGE_MODEL=gemini-3-pro-image-preview
```

## ✅ 验证安装

运行测试脚本：

```bash
cd trend_fetcher
python test_sticker_pack.py
```

如果所有测试通过，说明安装成功！

## 🚀 下一步

- 阅读 [完整文档](./sticker-pack-generator.md)
- 查看 [API 示例](./sticker-pack-api-examples.md)
- 了解 [故障排除](./sticker-pack-troubleshooting.md)

## 💡 提示

1. **首次使用**：建议先生成 10-20 张测试，确认配置正确
2. **网络问题**：如果生成失败，检查网络连接和 API 配置
3. **批量生成**：大量生成时建议分批进行
4. **保存结果**：定期备份 `output/` 目录

## 🎉 开始创作

现在你已经准备好了！选择一个科技主题，开始生成你的第一个贴纸包吧！

```bash
# Windows 用户
start_sticker_pack.bat

# Linux/Mac 用户
bash start_sticker_pack.sh

# 或直接运行
cd trend_fetcher
python sticker_pack_webui.py
```

祝你创作愉快！🎨
