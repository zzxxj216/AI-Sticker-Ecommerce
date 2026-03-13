# 贴纸包生成器 - 常见问题与故障排除

## 常见问题

### 1. API 配置问题

#### Q: 提示 "未配置 ANTHROPIC_API_KEY"

**原因**: 未在 `.env` 文件中配置 Claude API Key

**解决方案**:
```bash
# 编辑 .env 文件
ANTHROPIC_API_KEY=sk-claude-your-key-here
ANTHROPIC_BASE_URL=https://esapi.top
```

#### Q: 提示 "未配置图片 API Key"

**原因**: 未配置 Gemini API Key

**解决方案**:
```bash
# 在 .env 中添加
IMAGE_API_KEY=AIzaSyB1DWP9g1DAiIwFuKz3c_74voTRLskl4BM
IMAGE_BASE_URL=https://generativelanguage.googleapis.com
IMAGE_MODEL=gemini-3.1-flash-image-preview
```

#### Q: API 调用失败，返回 401/403

**可能原因**:
1. API Key 错误或过期
2. API Key 没有权限
3. Base URL 配置错误

**解决方案**:
```python
# 测试 Claude API
from anthropic import Anthropic
client = Anthropic(api_key="your-key", base_url="your-base-url")
response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=100,
    messages=[{"role": "user", "content": "Hello"}]
)
print(response.content[0].text)

# 测试 Gemini API
import requests
url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image-preview:generateContent?key=YOUR_KEY"
response = requests.post(url, json={
    "contents": [{"parts": [{"text": "test"}]}]
})
print(response.status_code)
```

### 2. 生成失败问题

#### Q: 创意生成失败，返回空列表

**可能原因**:
1. Claude API 调用失败
2. 响应格式解析错误
3. 网络超时

**解决方案**:
```python
# 查看详细错误日志
import logging
logging.basicConfig(level=logging.DEBUG)

from agents.sticker_pack_generator import StickerPackGenerator
generator = StickerPackGenerator()
result = generator.generate_pack(theme="测试", total_count=10)
```

#### Q: 图片生成失败率高

**可能原因**:
1. API 限流（429 错误）
2. 提示词不符合要求
3. 网络不稳定

**解决方案**:
```python
# 1. 降低并发数
# 在 sticker_pack_generator.py 中修改
image_results = image_gen.generate_batch(
    sticker_ideas=ideas,
    max_workers=2  # 从 3 降到 2
)

# 2. 增加重试次数
# 在 image_generator.py 中修改
response = self._call_api_with_retry(
    full_prompt,
    max_retries=5,  # 从 3 增加到 5
    ref_data=ref_data
)

# 3. 增加超时时间
# 在 .env 中设置
IMAGE_TIMEOUT=180  # 从 120 增加到 180 秒
```

#### Q: 部分贴纸生成成功，部分失败

**这是正常现象**。由于网络波动、API 限流等原因，可能会有少量失败。

**查看失败原因**:
```python
result = generator.generate_pack(theme="AI", total_count=40)

# 查看失败的贴纸
failed = [idea for idea in result['ideas'] if not idea.get('success')]
for idea in failed:
    print(f"{idea['title']}: {idea.get('error', '未知错误')}")
```

### 3. 性能问题

#### Q: 生成速度太慢

**优化方案**:

1. **增加并发数**（注意 API 限流）:
```python
# 在 sticker_pack_generator.py 中
image_results = image_gen.generate_batch(
    sticker_ideas=ideas,
    max_workers=5  # 增加到 5
)
```

2. **减少生成数量**:
```bash
python sticker_pack_cli.py --theme "AI" --count 20  # 从 40 减到 20
```

3. **使用更快的模型**:
```bash
# 在 .env 中
IMAGE_MODEL=gemini-3.1-flash-image-preview  # 最快
# IMAGE_MODEL=gemini-3-pro-image-preview    # 更慢但质量更高
```

#### Q: 内存占用过高

**解决方案**:
```python
# 分批生成
def generate_in_batches(theme, total_count, batch_size=20):
    generator = StickerPackGenerator()
    all_results = []

    for i in range(0, total_count, batch_size):
        count = min(batch_size, total_count - i)
        print(f"生成批次 {i//batch_size + 1}: {count} 张")

        result = generator.generate_pack(
            theme=theme,
            total_count=count
        )
        all_results.extend(result['ideas'])

    return all_results

# 使用
results = generate_in_batches("AI人工智能", total_count=100, batch_size=20)
```

### 4. 输出问题

#### Q: 找不到生成的图片

**检查输出目录**:
```python
from config import config
print(f"图片目录: {config.IMAGE_OUTPUT_DIR}")

# 查看今天的图片
from pathlib import Path
from datetime import datetime
today = datetime.now().strftime("%Y%m%d")
image_dir = config.IMAGE_OUTPUT_DIR / today
print(f"今日图片: {list(image_dir.glob('*.png'))}")
```

#### Q: JSON 结果文件损坏

**原因**: 生成过程中断或磁盘空间不足

**解决方案**:
```python
import json
from pathlib import Path

# 尝试修复 JSON
result_file = Path("output/sticker_packs/pack_xxx.json")
try:
    with open(result_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print("JSON 文件正常")
except json.JSONDecodeError as e:
    print(f"JSON 损坏: {e}")
    # 手动修复或重新生成
```

### 5. 依赖问题

#### Q: 导入错误 "No module named 'anthropic'"

**解决方案**:
```bash
pip install anthropic
```

#### Q: 导入错误 "No module named 'gradio'"

**解决方案**:
```bash
pip install gradio
```

#### Q: 版本冲突

**解决方案**:
```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 6. Web UI 问题

#### Q: Web UI 无法访问

**检查**:
1. 端口是否被占用
2. 防火墙是否阻止

**解决方案**:
```bash
# 更换端口
python sticker_pack_webui.py --port 8080

# 检查端口占用
netstat -ano | findstr :7860  # Windows
lsof -i :7860  # Linux/Mac
```

#### Q: Web UI 生成时卡住

**可能原因**: 浏览器超时

**解决方案**:
1. 使用命令行模式
2. 减少生成数量
3. 刷新页面重试

### 7. 提示词问题

#### Q: 生成的贴纸不符合预期

**优化提示词**:

编辑 `sticker_pack_generator.py` 中的 `_build_creative_prompt` 方法:

```python
def _build_creative_prompt(self, theme, text_count, element_count, hybrid_count):
    return f"""你是一个专业的贴纸设计师...

主题: {theme}

**额外要求**:
- 风格要可爱、年轻化  # 添加风格要求
- 适合年轻人使用      # 添加目标用户
- 色彩鲜艳明快        # 添加色彩要求

请为这个主题设计...
"""
```

#### Q: 图片提示词不够详细

**增强提示词**:
```python
# 在 _build_creative_prompt 中添加更详细的 image_prompt 指导
**image_prompt 编写要点:**
- 使用英文
- 必须包含: 主体描述 + 风格 + 颜色 + 构图
- 示例: "Cute robot character with big eyes, kawaii style, pastel colors (pink, blue, purple), centered composition, white background, sticker art"
- 对于文本类型: 描述字体样式、文字效果、背景
- 对于元素类型: 描述图形细节、材质、光影
- 对于组合类型: 描述文字和图形的位置关系
```

## 故障排除流程

### 步骤 1: 检查环境

```bash
# 检查 Python 版本
python --version  # 应该 >= 3.8

# 检查依赖
pip list | grep anthropic
pip list | grep gradio

# 检查 .env 配置
cat .env  # Linux/Mac
type .env  # Windows
```

### 步骤 2: 测试 API 连接

```python
# test_api.py
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import config
import anthropic

print("测试 Claude API...")
try:
    client = anthropic.Anthropic(
        api_key=config.ANTHROPIC_API_KEY,
        base_url=config.ANTHROPIC_BASE_URL if config.ANTHROPIC_BASE_URL else None
    )
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=100,
        messages=[{"role": "user", "content": "Hello"}]
    )
    print("✓ Claude API 正常")
except Exception as e:
    print(f"✗ Claude API 失败: {e}")

print("\n测试 Gemini API...")
try:
    from image_generator import StickerImageGenerator
    gen = StickerImageGenerator()
    print("✓ Gemini API 配置正常")
except Exception as e:
    print(f"✗ Gemini API 失败: {e}")
```

### 步骤 3: 运行小规模测试

```bash
cd trend_fetcher
python sticker_pack_cli.py --theme "测试" --count 5
```

### 步骤 4: 查看日志

```python
# 启用详细日志
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### 步骤 5: 联系支持

如果以上方法都无法解决问题，请提供以下信息:

1. 错误信息（完整堆栈跟踪）
2. Python 版本
3. 依赖版本 (`pip list`)
4. 操作系统
5. 配置文件（隐藏敏感信息）

## 最佳实践

1. **定期备份**: 定期备份 `output/` 目录
2. **监控配额**: 注意 API 使用配额，避免超限
3. **批量生成**: 大量生成时分批进行，避免一次性失败
4. **错误重试**: 对失败的贴纸单独重新生成
5. **版本控制**: 使用 git 管理配置和代码

## 性能基准

参考性能指标（40张贴纸）:

- **创意生成**: 10-20秒
- **图片生成**: 80-120秒（并发3）
- **总耗时**: 90-140秒
- **成功率**: 90-95%

如果性能明显低于这些指标，请检查网络连接和 API 配置。
