# 贴纸包生成器 API 示例

## Python API 使用示例

### 1. 基础用法

```python
from agents.sticker_pack_generator import StickerPackGenerator

# 初始化生成器
generator = StickerPackGenerator()

# 生成贴纸包
result = generator.generate_pack(
    theme="AI人工智能",
    total_count=40
)

# 查看结果
print(f"成功: {result['success_count']}/{result['total_count']}")
print(f"结果文件: {result['result_file']}")
```

### 2. 自定义类型占比

```python
# 生成更多文本类型的贴纸
result = generator.generate_pack(
    theme="区块链",
    total_count=50,
    text_ratio=0.5,      # 50% 纯文本
    element_ratio=0.25,  # 25% 元素
    hybrid_ratio=0.25    # 25% 组合
)
```

### 3. 批量生成多个主题

```python
themes = [
    "AI人工智能",
    "区块链",
    "元宇宙",
    "量子计算",
    "云计算"
]

generator = StickerPackGenerator()
results = []

for theme in themes:
    print(f"\n正在生成: {theme}")
    result = generator.generate_pack(
        theme=theme,
        total_count=30
    )
    results.append(result)
    print(f"完成: {result['success_count']}/30")

# 汇总统计
total_success = sum(r['success_count'] for r in results)
total_count = sum(r['total_count'] for r in results)
print(f"\n总计: {total_success}/{total_count} 张贴纸")
```

### 4. 访问生成的贴纸数据

```python
result = generator.generate_pack(theme="5G通信", total_count=20)

# 遍历所有成功生成的贴纸
for idea in result['ideas']:
    if idea.get('success'):
        print(f"标题: {idea['title']}")
        print(f"类型: {idea['type']}")
        print(f"文字: {idea['text_content']}")
        print(f"图片: {idea['image_path']}")
        print(f"大小: {idea['size_kb']} KB")
        print("-" * 40)
```

### 5. 按类型筛选贴纸

```python
result = generator.generate_pack(theme="物联网", total_count=30)

# 只获取纯文本贴纸
text_stickers = [
    idea for idea in result['ideas']
    if idea.get('type') == 'text' and idea.get('success')
]

print(f"纯文本贴纸: {len(text_stickers)} 张")
for sticker in text_stickers:
    print(f"  - {sticker['title']}: {sticker['text_content']}")

# 只获取元素贴纸
element_stickers = [
    idea for idea in result['ideas']
    if idea.get('type') == 'element' and idea.get('success')
]

print(f"\n元素贴纸: {len(element_stickers)} 张")
```

### 6. 错误处理

```python
try:
    result = generator.generate_pack(
        theme="大数据",
        total_count=40
    )

    if result.get('success', True):
        print(f"✓ 生成成功: {result['success_count']} 张")
    else:
        print(f"✗ 生成失败: {result.get('error')}")

    # 检查失败的贴纸
    failed = [
        idea for idea in result['ideas']
        if not idea.get('success')
    ]

    if failed:
        print(f"\n失败的贴纸 ({len(failed)} 张):")
        for idea in failed:
            print(f"  - {idea['title']}: {idea.get('error', '未知错误')}")

except Exception as e:
    print(f"生成过程出错: {e}")
```

### 7. 导出为不同格式

```python
import json
import csv
from pathlib import Path

result = generator.generate_pack(theme="机器学习", total_count=30)

# 导出为 CSV
csv_file = Path("stickers.csv")
with open(csv_file, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=[
        'index', 'type', 'title', 'text_content',
        'image_path', 'success'
    ])
    writer.writeheader()
    for idea in result['ideas']:
        writer.writerow({
            'index': idea['index'],
            'type': idea['type'],
            'title': idea['title'],
            'text_content': idea.get('text_content', ''),
            'image_path': idea.get('image_path', ''),
            'success': idea.get('success', False)
        })

print(f"已导出到: {csv_file}")

# 导出简化版 JSON
simple_result = {
    'theme': result['theme'],
    'total': result['total_count'],
    'success': result['success_count'],
    'stickers': [
        {
            'title': idea['title'],
            'type': idea['type'],
            'image': idea.get('filename', '')
        }
        for idea in result['ideas'] if idea.get('success')
    ]
}

simple_json = Path("stickers_simple.json")
with open(simple_json, 'w', encoding='utf-8') as f:
    json.dump(simple_result, f, ensure_ascii=False, indent=2)

print(f"已导出到: {simple_json}")
```

### 8. 集成到现有工作流

```python
from agents.sticker_pack_generator import StickerPackGenerator
from datetime import datetime

class StickerWorkflow:
    """贴纸生成工作流"""

    def __init__(self):
        self.generator = StickerPackGenerator()
        self.history = []

    def generate_daily_pack(self, theme: str):
        """生成每日贴纸包"""
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始生成: {theme}")

        result = self.generator.generate_pack(
            theme=theme,
            total_count=40
        )

        self.history.append({
            'date': datetime.now().isoformat(),
            'theme': theme,
            'success_count': result['success_count'],
            'result_file': result['result_file']
        })

        return result

    def get_statistics(self):
        """获取历史统计"""
        total_packs = len(self.history)
        total_stickers = sum(h['success_count'] for h in self.history)

        return {
            'total_packs': total_packs,
            'total_stickers': total_stickers,
            'avg_per_pack': total_stickers / total_packs if total_packs > 0 else 0,
            'history': self.history
        }

# 使用工作流
workflow = StickerWorkflow()

# 生成多个主题
themes = ["AI人工智能", "区块链", "元宇宙"]
for theme in themes:
    workflow.generate_daily_pack(theme)

# 查看统计
stats = workflow.get_statistics()
print(f"\n总计生成 {stats['total_packs']} 个贴纸包")
print(f"总计 {stats['total_stickers']} 张贴纸")
print(f"平均每包 {stats['avg_per_pack']:.1f} 张")
```

### 9. 使用配置文件

```python
import yaml

# config.yaml
"""
themes:
  - name: "AI人工智能"
    count: 40
    text_ratio: 0.3
    element_ratio: 0.35
    hybrid_ratio: 0.35

  - name: "区块链"
    count: 50
    text_ratio: 0.4
    element_ratio: 0.3
    hybrid_ratio: 0.3
"""

# 读取配置并生成
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

generator = StickerPackGenerator()

for theme_config in config['themes']:
    result = generator.generate_pack(
        theme=theme_config['name'],
        total_count=theme_config['count'],
        text_ratio=theme_config['text_ratio'],
        element_ratio=theme_config['element_ratio'],
        hybrid_ratio=theme_config['hybrid_ratio']
    )
    print(f"{theme_config['name']}: {result['success_count']} 张")
```

### 10. 异步生成（高级）

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def generate_async(theme: str, count: int):
    """异步生成贴纸包"""
    loop = asyncio.get_event_loop()
    generator = StickerPackGenerator()

    # 在线程池中运行
    result = await loop.run_in_executor(
        None,
        generator.generate_pack,
        theme,
        count
    )

    return result

async def generate_multiple_async(themes: list):
    """并发生成多个主题"""
    tasks = [
        generate_async(theme, 30)
        for theme in themes
    ]

    results = await asyncio.gather(*tasks)
    return results

# 使用异步生成
themes = ["AI人工智能", "区块链", "元宇宙", "量子计算"]
results = asyncio.run(generate_multiple_async(themes))

for result in results:
    print(f"{result['theme']}: {result['success_count']} 张")
```

## 命令行 API

### 基础命令

```bash
# 交互式模式
python sticker_pack_cli.py

# 快速生成
python sticker_pack_cli.py --theme "AI人工智能" --count 40

# 自定义占比
python sticker_pack_cli.py --theme "区块链" --count 50 \
  --text-ratio 0.4 --element-ratio 0.3 --hybrid-ratio 0.3
```

### 批处理脚本

```bash
#!/bin/bash
# batch_generate.sh

themes=("AI人工智能" "区块链" "元宇宙" "量子计算" "云计算")

for theme in "${themes[@]}"; do
    echo "生成主题: $theme"
    python sticker_pack_cli.py --theme "$theme" --count 30
    echo "---"
done

echo "全部完成！"
```

## REST API 封装（可选）

```python
from flask import Flask, request, jsonify
from agents.sticker_pack_generator import StickerPackGenerator

app = Flask(__name__)
generator = StickerPackGenerator()

@app.route('/api/generate', methods=['POST'])
def generate_pack():
    """生成贴纸包 API"""
    data = request.json

    theme = data.get('theme')
    if not theme:
        return jsonify({'error': '缺少主题参数'}), 400

    result = generator.generate_pack(
        theme=theme,
        total_count=data.get('count', 40),
        text_ratio=data.get('text_ratio', 0.3),
        element_ratio=data.get('element_ratio', 0.35),
        hybrid_ratio=data.get('hybrid_ratio', 0.35)
    )

    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

使用 REST API:

```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"theme": "AI人工智能", "count": 40}'
```
