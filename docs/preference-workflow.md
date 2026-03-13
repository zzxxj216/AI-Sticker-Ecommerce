# 智能偏好工作流程

## 工作流程设计

### 阶段 1: 偏好学习期（前 5-10 次使用）
```
用户输入 → 完整维度收集 → 生成图片 → 保存选择记录
```

### 阶段 2: 智能推荐期（积累足够数据后）
```
用户输入 → 智能分析偏好 → 生成推荐配置 → 用户确认/调整 → 生成图片
```

## 实现方案

### 方案 A: 数据库存储（推荐）

**优点**：
- 持久化存储
- 支持多用户
- 可以做数据分析
- 易于更新和查询

**实现**：
```sql
-- 用户偏好表
CREATE TABLE user_preferences (
  id INT PRIMARY KEY,
  user_id VARCHAR(50),
  category VARCHAR(50),
  preference_data JSON,
  usage_count INT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- 历史选择记录表
CREATE TABLE generation_history (
  id INT PRIMARY KEY,
  user_id VARCHAR(50),
  category VARCHAR(50),
  dimensions JSON,
  image_url VARCHAR(255),
  created_at TIMESTAMP
);
```

### 方案 B: 本地文件存储（简单场景）

**优点**：
- 实现简单
- 无需数据库
- 适合单用户或小规模

**实现**：
```javascript
// preferences/pet_stickers.json
{
  "learned_preferences": {
    "style": {
      "cute_cartoon": 12,
      "realistic": 2,
      "minimalist": 1
    },
    "colors": {
      "pastel_pink": 10,
      "soft_blue": 8,
      "warm_yellow": 7
    }
  },
  "confidence_level": "high" // low/medium/high
}
```

## 智能推荐逻辑

### 1. 置信度判断
```javascript
function shouldUseSmartRecommendation(category, userId) {
  const history = getGenerationHistory(category, userId);
  
  // 至少需要 5 次历史记录
  if (history.length < 5) return false;
  
  // 检查选择的一致性（80%以上选择相似）
  const consistency = calculateConsistency(history);
  if (consistency < 0.8) return false;
  
  return true;
}
```

### 2. 智能推荐生成
```javascript
function generateSmartRecommendation(userInput, preferences) {
  return {
    "subject": userInput, // 用户输入的主题
    "style": preferences.style.top_choice,
    "colors": preferences.colors.top_3,
    "elements": preferences.elements.frequent,
    "mood": preferences.mood.default,
    "confidence": "85%",
    "reasoning": "基于您过去 15 次宠物贴纸的生成记录"
  };
}
```

### 3. 用户确认界面
```
┌─────────────────────────────────────────┐
│ 🎨 智能推荐配置                          │
├─────────────────────────────────────────┤
│ 主题: 可爱的小猫                         │
│ 风格: 卡通风格 (基于您的偏好)            │
│ 配色: 粉色系 + 蓝色系                    │
│ 元素: 爪印、爱心、星星                   │
│ 情绪: 活泼可爱                           │
│                                          │
│ 置信度: ⭐⭐⭐⭐⭐ 85%                    │
│                                          │
│ [✓ 直接生成]  [✏️ 调整配置]  [❌ 重新选择] │
└─────────────────────────────────────────┘
```

## n8n 工作流实现

### 工作流结构
```
Webhook (用户请求)
  ↓
[查询用户偏好]
  ↓
IF: 是否有足够偏好数据？
  ├─ YES → [生成智能推荐] → [返回确认界面]
  │         ↓
  │       Webhook (用户确认)
  │         ↓
  │       [调用 AI 生成图片]
  │
  └─ NO → [完整维度收集] → [调用 AI 生成图片]
           ↓
         [保存选择记录]
```

### 关键节点配置

#### 1. 查询偏好节点（Postgres/MySQL）
```javascript
SELECT preference_data, usage_count
FROM user_preferences
WHERE user_id = '{{$json.body.user_id}}'
  AND category = 'pet_stickers'
```

#### 2. 判断节点（IF）
```javascript
{{$json.usage_count >= 5 && $json.confidence_level === 'high'}}
```

#### 3. 智能推荐节点（Code）
```javascript
const preferences = $json.preference_data;
const userInput = $json.body.subject;

// 生成推荐配置
const recommendation = {
  subject: userInput,
  style: preferences.style.top_choice,
  colors: preferences.colors.slice(0, 3),
  elements: preferences.elements.frequent,
  mood: preferences.mood.default,
  confidence: calculateConfidence(preferences)
};

return [{
  json: {
    recommendation,
    requires_confirmation: true
  }
}];
```

#### 4. 保存记录节点（Postgres/MySQL）
```javascript
INSERT INTO generation_history 
  (user_id, category, dimensions, image_url)
VALUES 
  ('{{$json.user_id}}', 'pet_stickers', 
   '{{$json.dimensions}}', '{{$json.image_url}}')
```

## 渐进式学习策略

### 冷启动阶段（0-5 次）
- 完整收集所有维度
- 每次保存用户选择
- 不做推荐

### 学习阶段（5-15 次）
- 开始分析偏好模式
- 提供"快速选择"选项
- 仍保留完整选项

### 成熟阶段（15+ 次）
- 默认使用智能推荐
- 一键确认生成
- 保留"自定义"入口

## 用户体验优化

### 1. 透明度
```
"我们注意到您经常选择卡通风格的粉色系宠物贴纸，
 是否使用这个配置快速生成？"
```

### 2. 可控性
```
始终提供"自定义"按钮，让用户可以完全控制
```

### 3. 反馈循环
```
生成后询问："这次的推荐准确吗？" 
→ 用于优化算法
```

## 实现优先级

### MVP（最小可行产品）
1. ✅ 保存用户每次的选择记录
2. ✅ 统计最常用的 3-5 个维度值
3. ✅ 当记录 ≥ 5 次时，显示"使用常用配置"按钮

### 进阶功能
1. 🔄 多场景偏好（宠物、节日、商务等）
2. 🔄 智能推荐置信度显示
3. 🔄 A/B 测试不同推荐策略

### 高级功能
1. 🚀 机器学习模型预测
2. 🚀 协同过滤（参考相似用户）
3. 🚀 时间序列分析（季节性偏好）
