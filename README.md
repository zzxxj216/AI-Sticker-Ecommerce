# AI Sticker Workbench

一站式 AI 贴纸电商内容工作台 —— 从热点发现、选题审核、贴纸生成到 Blog 发布的全链路自动化平台。

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 核心功能

### 热点发现与数据引擎
- 多源热点抓取：NewsAPI、Google Trends、Reddit、RSS、TikTok Creative Center
- AI 驱动的贴纸机会评估管线（7 维打分 + 硬过滤 + 母型映射）
- TikTok 话题 Playwright 自动爬取与 AI 批量审核

### 选题审核与 Brief 生成
- 人工审核看板（采纳 / 拒绝 / 归档）
- AI 自动生成 Trend Brief（主题、风格、配色、目标受众）
- 审核通过后自动进入待生产队列

### AI 贴纸包生成
- 多 Agent 管线：Planner → Designer → Prompt Builder → Image Generation → QC
- 支持 OpenAI GPT / Claude / Gemini 多模型协作
- Gemini 图片生成，支持并发控制（信号量限流）
- 每个卡包约 6-8 张贴纸，批量生成多个卡包

### AI 对话创作
- **AI 卡贴创作**：基于 OpenAI Function Calling 的对话式贴纸生成，支持工具调用
- **AI Blog 创作**：ReAct Agent 架构，规划 → 写作 → 审稿 → 配图全流程
- 对话历史持久化，支持会话恢复继续创作

### Blog 管理与发布
- Markdown 编辑与预览
- AI 自动生成博客配图（Gemini）
- 一键发布到 Shopify（草稿 / 正式）
- Shopify HTML 转换与图片上传

### 运维工作台
- 首页仪表盘：待审核、待生产、运行中任务等关键指标
- 生产任务监控：实时日志、进度追踪
- 卡贴画廊 & 卡包管理：分页浏览、打包下载
- 飞书 OIDC 登录 / 本地开发模式自动切换

---

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Jinja2 + Starlette |
| AI 模型 | OpenAI GPT-4o / Claude Sonnet / Gemini Pro & Flash |
| 图片生成 | Google Gemini Imagen |
| 数据库 | SQLite（零依赖部署） |
| 任务调度 | APScheduler（每日管线） |
| 浏览器自动化 | Playwright（TikTok 爬取） |
| 认证 | 飞书 OIDC / 本地 Auto-Dev |
| 电商集成 | Shopify Admin API |
| 部署 | Cloudflare Tunnel 内网穿透 |

---

## 项目结构

```
AI-Sticker-Ecommerce/
├── web_app.py                  # 启动入口
├── requirements.txt            # Python 依赖
├── config/                     # YAML 配置文件
│   ├── default.yaml
│   ├── development.yaml
│   ├── production.yaml
│   └── store_profile.yaml      # Shopify 店铺画像
│
├── src/
│   ├── core/                   # 核心模块
│   │   ├── config.py           # 配置加载（YAML + .env）
│   │   ├── constants.py        # 全局常量与枚举
│   │   ├── logger.py           # 日志封装
│   │   └── exceptions.py       # 自定义异常
│   │
│   ├── models/                 # Pydantic 数据模型
│   │   ├── ops.py              # 趋势、Brief、任务、输出
│   │   ├── blog.py             # 博客草稿与配置
│   │   ├── sticker_pack.py     # 贴纸包管线数据结构
│   │   └── ...
│   │
│   ├── services/
│   │   ├── ai/                 # AI 服务封装
│   │   │   ├── claude_service.py
│   │   │   ├── gemini_service.py
│   │   │   ├── openai_service.py
│   │   │   └── prompt_builder.py
│   │   │
│   │   ├── batch/              # 批量生成管线
│   │   │   ├── sticker_pipeline.py   # Planner→Designer→Builder→QC
│   │   │   ├── sticker_prompts.py    # 多 Agent Prompt 模板
│   │   │   ├── image_generation.py   # 图片生成与质检
│   │   │   └── ...
│   │   │
│   │   ├── blog/               # Blog 多 Agent 服务
│   │   │   ├── orchestrator.py       # 全流程编排
│   │   │   ├── planner_agent.py      # 大纲规划
│   │   │   ├── writer_agent.py       # 写作 Agent
│   │   │   ├── reviewer_agent.py     # 审稿 Agent
│   │   │   ├── blog_image_generator.py
│   │   │   ├── shopify_converter.py  # Markdown → Shopify HTML
│   │   │   └── shopify_publisher.py  # Shopify API 发布
│   │   │
│   │   ├── ops/                # 运维数据层
│   │   │   ├── db.py                 # SQLite 数据库操作
│   │   │   ├── trend_service.py      # 趋势管线编排
│   │   │   ├── job_service.py        # 生成任务管理
│   │   │   └── sync_service.py       # 数据同步
│   │   │
│   │   ├── tools/              # 对话 Agent（Function Calling）
│   │   │   ├── sticker_agent.py      # 贴纸对话 Agent
│   │   │   └── blog_agent.py         # Blog 对话 Agent
│   │   │
│   │   └── sticker/            # 贴纸核心服务
│   │       ├── pack_generator.py
│   │       ├── style_analyzer.py
│   │       └── theme_generator.py
│   │
│   ├── web/                    # Web 应用层
│   │   ├── app.py              # FastAPI 路由与中间件
│   │   ├── auth_middleware.py   # 认证中间件
│   │   ├── feishu_auth.py      # 飞书 OIDC 登录
│   │   ├── static/             # CSS / JS 静态资源
│   │   └── templates/          # Jinja2 页面模板（19 个）
│   │
│   └── utils/                  # 工具函数
│
├── trend_fetcher/              # 热点数据抓取子模块
│   ├── main.py                 # 抓取入口
│   ├── topic_pipeline.py       # TikTok AI 审核管线
│   ├── trend_db.py             # TikTok 数据表
│   ├── fetchers/               # 各数据源爬虫
│   │   ├── news_api.py
│   │   ├── google_trends.py
│   │   ├── reddit.py
│   │   ├── rss_feeds.py
│   │   └── tiktok.py           # Playwright 爬取
│   └── sticker_pipeline/       # 贴纸机会评估管线
│       ├── pipeline.py         # 总编排
│       ├── hard_filter.py      # 硬过滤
│       ├── opportunity_scorer.py
│       └── brief_builder.py    # Trend Brief 生成
│
├── deploy/
│   └── setup_macmini.sh        # Mac Mini 一键部署（含内网穿透）
│
├── data/                       # 运行时数据（SQLite 等）
├── output/                     # 生成产物（贴纸、博客、HTML）
└── logs/                       # 日志文件
```

---

## 快速开始

### 环境要求

- Python 3.10+
- 至少一个 AI API Key（OpenAI / Gemini）

### 本地开发

```bash
# 1. 克隆项目
git clone https://github.com/zzxxj216/AI-Sticker-Ecommerce.git
cd AI-Sticker-Ecommerce

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key

# 5. 启动
python web_app.py
```

访问 http://localhost:8888

### Mac Mini 部署（含内网穿透）

```bash
chmod +x deploy/setup_macmini.sh
./deploy/setup_macmini.sh
./start.sh
```

启动后自动：
1. 创建虚拟环境并安装依赖
2. 生成 `.env` 配置文件
3. 启动 Cloudflare Tunnel 内网穿透
4. 自动更新飞书回调地址
5. 启动 Web 服务

管理命令：

| 命令 | 说明 |
|------|------|
| `./start.sh` | 启动服务 + 穿透 |
| `./stop.sh` | 停止所有 |
| `./restart.sh` | 重启所有 |
| `./status.sh` | 查看状态和地址 |

---

## 环境变量

| 变量 | 说明 | 必填 |
|------|------|:----:|
| `OPENAI_API_KEY` | OpenAI API 密钥 | 是 |
| `OPENAI_BASE_URL` | OpenAI 代理地址 | 否 |
| `IMAGE_API_KEY` | Google Gemini API 密钥（图片生成） | 是 |
| `ANTHROPIC_API_KEY` | Claude API 密钥 | 否 |
| `SHOPIFY_STORE_DOMAIN` | Shopify 店铺域名 | 否 |
| `SHOPIFY_CLIENT_SECRET` | Shopify API 密钥 | 否 |
| `FEISHU_H5_APP_ID` | 飞书应用 ID（留空则自动开发模式） | 否 |
| `FEISHU_H5_APP_SECRET` | 飞书应用密钥 | 否 |
| `NEWS_API_KEY` | NewsAPI 密钥（热点抓取） | 否 |
| `SESSION_SECRET` | Session 签名密钥 | 是 |

完整变量列表参见 `.env.example`。

---

## 页面导航

| 分组 | 页面 | 说明 |
|------|------|------|
| 工作台 | 首页仪表盘 | 关键指标、快捷入口、最近动态 |
| 热点发现 | 新闻动态 | NewsAPI / RSS 热点新闻 |
| | TikTok 动态 | TikTok 话题爬取结果 |
| | 话题总览 | 聚合所有数据源的话题 |
| 选题审核 | 审核看板 | 待审核趋势一览 |
| | 待生产素材 | 已采纳、待生成的素材 |
| | 归档记录 | 历史审核归档 |
| 创作中心 | AI 卡贴创作 | 对话式贴纸生成 |
| | AI Blog 创作 | 对话式博客生成 |
| | 生产任务 | 任务监控与日志 |
| 资产管理 | 卡贴画廊 | 已生成贴纸浏览 |
| | 卡包管理 | 贴纸包下载与管理 |
| | Blog 管理 | 博客查看、编辑、发布 |

---

## 飞书集成

### 配置步骤

1. 在 [飞书开放平台](https://open.feishu.cn) 创建企业自建应用
2. 添加「网页应用」能力
3. 权限管理 → 开通 `contact:user.base:readonly`
4. 安全设置 → 重定向 URL：`https://你的域名/auth/feishu/callback`
5. 创建版本 → 提交审核 → 发布

### 开发模式

不填 `FEISHU_H5_APP_ID` 或设置 `FEISHU_H5_AUTO_DEV=true`，系统自动以 "Local Dev" 身份登录，无需飞书配置。

---

## 许可证

MIT License
