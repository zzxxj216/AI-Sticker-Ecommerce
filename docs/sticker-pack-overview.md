# 贴纸包生成器 - 项目总览

## 📦 项目结构

```
AI-Sticker-Ecommerce/
├── trend_fetcher/
│   ├── agents/
│   │   ├── sticker_pack_generator.py    # 核心生成器
│   │   └── ...
│   ├── sticker_pack_cli.py              # 命令行工具
│   ├── sticker_pack_webui.py            # Web UI
│   ├── test_sticker_pack.py             # 自动化测试
│   ├── image_generator.py               # 图片生成模块（已有）
│   ├── config.py                        # 配置管理（已有）
│   └── output/
│       ├── sticker_packs/               # 生成结果（JSON）
│       └── images/                      # 生成的图片
├── docs/
│   ├── sticker-pack-generator.md        # 使用文档
│   ├── sticker-pack-api-examples.md     # API 示例
│   └── sticker-pack-troubleshooting.md  # 故障排除
├── start_sticker_pack.bat               # Windows 启动脚本
├── start_sticker_pack.sh                # Linux/Mac 启动脚本
└── .env                                 # 环境配置
```

## 🎯 核心功能

### 1. 自动化贴纸生成
- 输入科技主题，自动生成 30-50 张贴纸
- 三种类型：纯文本、元素、组合
- 使用 Claude 生成创意，Gemini 生成图片

### 2. 灵活配置
- 自定义贴纸数量（10-100）
- 自定义类型占比
- 支持批量生成多个主题

### 3. 多种使用方式
- 命令行交互式
- 命令行快速生成
- Web UI（推荐）
- Python API

### 4. 完整的输出
- JSON 格式结果文件
- 高质量 PNG 图片
- 详细统计信息

## 🚀 快速开始

### Windows 用户

双击运行 `start_sticker_pack.bat`，选择启动方式。

### Linux/Mac 用户

```bash
bash start_sticker_pack.sh
```

### 手动启动

```bash
# Web UI（推荐）
cd trend_fetcher
python sticker_pack_webui.py

# 命令行
python sticker_pack_cli.py --theme "AI人工智能" --count 40

# 测试
python test_sticker_pack.py
```

## 📊 工作流程

```
用户输入主题
    ↓
Claude 生成创意
    ↓
Gemini 并发生成图片
    ↓
保存结果（JSON + PNG）
    ↓
展示统计信息
```

## 🔧 技术栈

- **语言**: Python 3.8+
- **AI 模型**:
  - Claude Sonnet 4.5（创意生成）
  - Gemini 3.1 Flash Image Preview（图片生成）
- **Web 框架**: Gradio
- **并发**: ThreadPoolExecutor
- **配置**: python-dotenv

## 📈 性能指标

| 指标 | 数值 |
|------|------|
| 40张贴纸总耗时 | 2-3 分钟 |
| 创意生成 | 10-20 秒 |
| 单张图片生成 | 3-5 秒 |
| 并发数 | 3 线程 |
| 成功率 | 90-95% |

## 🎨 贴纸类型

### 1. 📝 纯文本贴纸（30%）
- 简短有力的文字
- 口号、梗、流行语
- 例如："AI赋能"、"代码改变世界"

### 2. 🎨 元素贴纸（35%）
- 主题相关的视觉元素
- 图标、符号、卡通形象
- 例如：机器人图标、芯片图案

### 3. 🔀 组合贴纸（35%）
- 文字与元素结合
- 相互呼应、融合
- 例如：带"AI"字样的机器人

## 📝 使用示例

### 示例 1: 快速生成

```bash
python sticker_pack_cli.py --theme "AI人工智能" --count 40
```

### 示例 2: 自定义占比

```bash
python sticker_pack_cli.py --theme "区块链" --count 50 \
  --text-ratio 0.4 --element-ratio 0.3 --hybrid-ratio 0.3
```

### 示例 3: Python API

```python
from agents.sticker_pack_generator import StickerPackGenerator

generator = StickerPackGenerator()
result = generator.generate_pack(theme="元宇宙", total_count=40)

print(f"成功: {result['success_count']}/40")
```

### 示例 4: 批量生成

```python
themes = ["AI人工智能", "区块链", "元宇宙"]
for theme in themes:
    result = generator.generate_pack(theme=theme, total_count=30)
    print(f"{theme}: {result['success_count']}/30")
```

## 🌟 主题推荐

### 热门科技主题
- AI人工智能
- 区块链
- 元宇宙
- 量子计算
- 云计算

### 新兴技术
- 5G通信
- 物联网
- 边缘计算
- 自动驾驶
- 机器学习

### 开发相关
- 前端开发
- 后端开发
- DevOps
- 微服务
- 容器化

## 📚 文档导航

- [使用文档](./sticker-pack-generator.md) - 详细使用说明
- [API 示例](./sticker-pack-api-examples.md) - 代码示例
- [故障排除](./sticker-pack-troubleshooting.md) - 常见问题

## 🔐 环境配置

在 `.env` 文件中配置：

```bash
# Claude API（创意生成）
ANTHROPIC_API_KEY=sk-claude-xxx
ANTHROPIC_BASE_URL=https://esapi.top

# Gemini API（图片生成）
IMAGE_API_KEY=AIzaSyB1DWP9g1DAiIwFuKz3c_74voTRLskl4BM
IMAGE_BASE_URL=https://generativelanguage.googleapis.com
IMAGE_MODEL=gemini-3.1-flash-image-preview

# 输出目录
OUTPUT_DIR=./output
```

## 🎯 设计理念

1. **自动化优先**: 最小化人工干预
2. **灵活配置**: 支持多种自定义选项
3. **高效并发**: 充分利用 API 并发能力
4. **容错设计**: 部分失败不影响整体
5. **易于使用**: 多种使用方式，降低门槛

## 🔄 与现有系统的关系

贴纸包生成器是一个**独立模块**，与现有的趋势分析系统完全解耦：

- **独立运行**: 不依赖趋势数据
- **独立配置**: 使用相同的 `.env` 但功能独立
- **独立输出**: 输出到 `output/sticker_packs/` 目录
- **可选集成**: 可以与趋势系统结合，根据热门趋势生成贴纸

## 🚧 未来扩展

### 计划功能
- [ ] 支持更多图片生成模型（DALL-E、Midjourney）
- [ ] 支持自定义风格模板
- [ ] 支持参考图批量上传
- [ ] 支持贴纸包预览和编辑
- [ ] 支持导出为贴纸包格式（Telegram、WeChat）
- [ ] 支持 AI 自动评分和筛选
- [ ] 支持多语言贴纸生成

### 集成建议
- 与趋势分析系统集成，自动生成热门话题贴纸
- 与电商系统集成，自动上架贴纸商品
- 与社交平台集成，自动发布贴纸

## 📞 支持

遇到问题？

1. 查看 [故障排除文档](./sticker-pack-troubleshooting.md)
2. 运行测试: `python test_sticker_pack.py`
3. 查看日志输出
4. 提交 Issue

## 📄 许可证

MIT License

---

**开始使用**: 运行 `start_sticker_pack.bat`（Windows）或 `bash start_sticker_pack.sh`（Linux/Mac）
