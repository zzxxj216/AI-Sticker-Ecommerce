# 🎨 AI 贴纸生成器

> 使用 AI 技术生成高质量贴纸，支持主题生成、风格分析和变种创作
>独立站用户使用多种内容格式支持
> 内部使用需要先根据具体风格生成相关卡包内容，一次可以生成多个卡包，每个卡包包含50张左右的图片，需要先生成预览版本，预览版把，保留相关prompt，确定相关没有问题后，再去生成相关详细版本。

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Ready-brightgreen.svg)]()

---

## ✨ 功能特性

### 📦 贴纸包生成
- 输入主题，批量生成 1-100 张贴纸
- 支持三种类型：纯文字、纯元素、组合
- 并发生成，2-3 分钟完成 40 张
- Claude 创意 + Gemini 图片生成

### 🔍 风格分析
- 上传贴纸，AI 深度分析风格特征
- 7 个维度：视觉风格、色彩、元素、情感等
- 详细分析报告和设计建议
- Claude Vision 视觉理解

### 🎭 变种生成
- 基于现有贴纸生成相似变种
- 可调变化程度：轻微、中等、较大
- 保持风格一致性
- 快速扩展贴纸库

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境

创建 `.env` 文件：

```bash
# Claude API
ANTHROPIC_API_KEY=your_claude_api_key

# Gemini API
GOOGLE_API_KEY=your_gemini_api_key

# 可选配置
OUTPUT_DIR=./output
LOG_LEVEL=INFO
```

### 3. 启动应用

#### 方式 1：Web UI（推荐）

```bash
python app.py
```

访问：http://localhost:7860

#### 方式 2：命令行

```bash
# 生成贴纸包
python cli.py generate -t "AI人工智能" -c 40

# 分析风格
python cli.py analyze sticker.png

# 生成变种
python cli.py variants sticker.png -c 5
```

---

## 📖 使用指南

### Web UI 使用

#### 贴纸包生成
1. 打开"贴纸包生成"标签页
2. 输入主题（如：AI人工智能、可爱猫咪）
3. 设置生成数量（1-100）
4. 调整高级选项（可选）
5. 点击"开始生成"
6. 等待完成，查看结果

#### 风格分析
1. 打开"风格分析"标签页
2. 上传贴纸图片
3. 点击"开始分析"
4. 查看详细分析结果

#### 变种生成
1. 打开"变种生成"标签页
2. 上传原始贴纸
3. 设置变种数量和变化程度
4. 点击"生成变种"
5. 查看生成的变种

### CLI 使用

#### 生成贴纸包

```bash
# 基础用法
python cli.py generate -t "AI人工智能" -c 40

# 完整参数
python cli.py generate \
  --theme "可爱猫咪" \
  --count 20 \
  --text-ratio 0.3 \
  --element-ratio 0.4 \
  --combined-ratio 0.3 \
  --workers 3
```

#### 分析风格

```bash
# 基础用法
python cli.py analyze sticker.png

# 保存结果
python cli.py analyze sticker.png --output analysis.json
```

#### 生成变种

```bash
# 基础用法
python cli.py variants sticker.png -c 5

# 完整参数
python cli.py variants sticker.png \
  --count 10 \
  --degree significant \
  --workers 3
```

---

## 🏗️ 项目架构

```
AI-Sticker-Ecommerce/
├── src/
│   ├── core/              # 核心模块
│   │   ├── config.py      # 配置管理
│   │   ├── constants.py   # 常量定义
│   │   ├── logger.py      # 日志系统
│   │   └── exceptions.py  # 异常定义
│   ├── models/            # 数据模型
│   │   ├── sticker.py     # 贴纸模型
│   │   ├── style.py       # 风格模型
│   │   └── session.py     # 会话模型
│   ├── services/          # 服务层
│   │   ├── ai/            # AI 服务
│   │   │   ├── claude_service.py
│   │   │   ├── gemini_service.py
│   │   │   └── prompt_builder.py
│   │   └── sticker/       # 贴纸服务
│   │       ├── pack_generator.py
│   │       └── style_analyzer.py
│   ├── ui/                # UI 层
│   │   ├── gradio_app.py  # Gradio 应用
│   │   └── components.py  # UI 组件
│   ├── cli/               # CLI 层
│   │   └── sticker_cli.py # 命令行工具
│   └── utils/             # 工具模块
│       ├── file_utils.py
│       ├── image_utils.py
│       └── validators.py
├── app.py                 # Web UI 启动脚本
├── cli.py                 # CLI 启动脚本
├── requirements.txt       # 依赖列表
├── .env                   # 环境配置
└── README.md             # 项目说明
```

---

## 💻 Python API

### 贴纸包生成

```python
from src.services.sticker import PackGenerator

# 初始化
generator = PackGenerator()

# 生成贴纸包
pack = generator.generate(
    theme="AI人工智能",
    count=40,
    text_ratio=0.3,
    element_ratio=0.4,
    combined_ratio=0.3,
    max_workers=3
)

print(f"成功: {pack.success_count}/{pack.total_count}")
print(f"输出: {pack.output_dir}")
```

### 风格分析

```python
from src.services.sticker import StyleAnalyzer

# 初始化
analyzer = StyleAnalyzer()

# 分析风格
analysis = analyzer.analyze("sticker.png")

print(f"视觉风格: {analysis.visual_style.value}")
print(f"色彩方案: {analysis.color_palette.value}")
print(f"主要颜色: {analysis.dominant_colors}")
```

### 变种生成

```python
from src.services.sticker import StyleAnalyzer
from src.core.constants import VariationDegree

analyzer = StyleAnalyzer()

# 一站式处理：分析 + 生成
result = analyzer.analyze_and_generate(
    image_path="sticker.png",
    variant_count=5,
    variation_degree=VariationDegree.MEDIUM
)

print(f"成功: {result['success_count']}/{result['total_count']}")
print(f"变种: {result['variant_paths']}")
```

---

## 📊 性能指标

| 功能 | 耗时 | 成功率 |
|------|------|--------|
| 贴纸包生成（40张） | 2-3 分钟 | 90-95% |
| 风格分析 | 10-15 秒 | 95%+ |
| 变种生成（5张） | 30-40 秒 | 90-95% |

---

## 🎯 使用场景

### 场景 1：新建贴纸包
```
输入主题 → 生成 40 张 → 挑选最佳 → 完成
```

### 场景 2：扩展现有贴纸
```
上传贴纸 → 分析风格 → 生成变种 → 扩展库
```

### 场景 3：完整工作流
```
生成初始包 → 挑选最佳 → 生成变种 → 获得 50+ 张
```

---

## ⚙️ 配置说明

### 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| ANTHROPIC_API_KEY | Claude API 密钥 | ✅ |
| GOOGLE_API_KEY | Gemini API 密钥 | ✅ |
| OUTPUT_DIR | 输出目录 | ❌ |
| LOG_LEVEL | 日志级别 | ❌ |

### 参数说明

**贴纸包生成**：
- `theme`: 主题（必填）
- `count`: 数量（1-100，默认 40）
- `text_ratio`: 纯文字比例（0-1，默认 0.3）
- `element_ratio`: 纯元素比例（0-1，默认 0.4）
- `combined_ratio`: 组合比例（0-1，默认 0.3）
- `workers`: 并发数（1-5，默认 3）

**变种生成**：
- `variant_count`: 变种数量（1-20，默认 5）
- `variation_degree`: 变化程度（slight/medium/significant）
- `workers`: 并发数（1-5，默认 3）

---

## 🔧 技术栈

- **Python** 3.8+
- **AI 模型**
  - Claude Opus 4.6（创意生成）
  - Claude Vision（风格分析）
  - Gemini Imagen 3（图片生成）
- **Web 框架** Gradio 4.0+
- **CLI 工具** Click + Rich
- **并发** ThreadPoolExecutor

---

## 📝 开发文档

- [快速开始指南](QUICKSTART.md) - 详细使用说明
- [项目重构文档](PROJECT_RESTRUCTURE.md) - 架构设计
- [服务迁移总结](SERVICE_MIGRATION_SUMMARY.md) - 开发历程

---

## 🐛 故障排除

### 常见问题

**Q: API 错误？**
- 检查 API Key 是否正确
- 检查 API 配额是否充足
- 查看日志文件：`logs/app.log`

**Q: 生成速度慢？**
- 减少并发数（workers）
- 分批生成
- 检查网络连接

**Q: 生成失败？**
- 查看错误信息
- 检查主题是否合适
- 尝试重新生成

---

## 📈 项目进度

- ✅ 基础架构（100%）
- ✅ AI 服务层（100%）
- ✅ 贴纸服务层（100%）
- ✅ UI 层（100%）
- ⏳ 测试（进行中）

**当前版本**: v1.0.0  
**状态**: 可用

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

## 📄 许可证

MIT License

---

## 🔗 相关链接

- [Claude API](https://console.anthropic.com)
- [Gemini API](https://aistudio.google.com/apikey)
- [Gradio 文档](https://gradio.app)

---

**开始你的贴纸创作之旅！** 🎨✨
