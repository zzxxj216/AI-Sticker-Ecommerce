# TikTok Shop 商品发布接口文档

## 📋 目录

- [接口概览](#接口概览)
- [认证与签名](#认证与签名)
- [商品管理接口](#商品管理接口)
- [辅助接口](#辅助接口)
- [完整示例](#完整示例)
- [错误处理](#错误处理)
- [常见问题](#常见问题)

---

## 接口概览

### 基础信息

- **API 基础地址**: `https://open-api.tiktokglobalshop.com`
- **API 版本**: `202309`
- **认证方式**: HMAC-SHA256 签名 + OAuth 2.0
- **请求格式**: JSON
- **响应格式**: JSON

### API 路径前缀

```python
_PRODUCT_PREFIX = "/product/202309"
```

所有商品相关接口都使用此前缀，例如：
- 创建商品: `POST /product/202309/products`
- 查询商品: `GET /product/202309/products/{product_id}`

---

## 认证与签名

### 1. 必需凭证

```python
# 主应用凭证
TIKTOK_APP_KEY = "your_app_key"
TIKTOK_APP_SECRET = "your_app_secret"
TIKTOK_ACCESS_TOKEN = "your_access_token"
TIKTOK_SHOP_CIPHER = "your_shop_cipher"  # 店铺加密ID
```

### 2. 签名算法

TikTok Shop API 使用 HMAC-SHA256 签名验证请求：

```python
def _generate_sign(app_secret: str, path: str, params: dict, body: str = "") -> str:
    """
    签名步骤:
    1. 排除 sign、access_token 参数
    2. 按 key 字母排序拼接: key1value1key2value2...
    3. sign_string = app_secret + path + sorted_params + body + app_secret
    4. HMAC-SHA256(app_secret, sign_string) → 小写十六进制
    """
    exclude_keys = {"sign", "access_token"}
    sorted_params = sorted(
        ((k, v) for k, v in params.items() if k not in exclude_keys),
        key=lambda x: x[0],
    )
    param_str = "".join(f"{k}{v}" for k, v in sorted_params)
    
    sign_string = f"{app_secret}{path}{param_str}{body}{app_secret}"
    signature = hmac.new(
        app_secret.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    return signature
```

### 3. 请求参数

每个请求必须包含以下参数：

```python
params = {
    "app_key": "your_app_key",
    "timestamp": str(int(time.time())),
    "shop_cipher": "your_shop_cipher",  # 大部分接口需要
    "sign": "calculated_signature",
    "access_token": "your_access_token"
}

headers = {
    "Content-Type": "application/json",
    "x-tts-access-token": "your_access_token"
}
```

---

## 商品管理接口

### 1. 创建商品 (Create Product)

**接口**: `POST /product/202309/products`

**功能**: 创建新商品并发布到 TikTok Shop

#### 请求示例

```python
async def create_product(data: dict[str, Any]) -> dict[str, Any]:
    return await api_request("POST", f"{_PRODUCT_PREFIX}/products", body=data)
```

#### 请求体结构

```json
{
  "title": "商品标题 (必需, 最多255字符)",
  "description": "HTML格式商品描述 (必需)",
  "category_id": "928016",
  "category_version": "v2",
  "brand_id": "品牌ID (可选)",
  "main_images": [
    {
      "uri": "tiktok://image_uri_from_upload_api"
    }
  ],
  "skus": [
    {
      "seller_sku": "唯一SKU编码",
      "price": {
        "amount": "16.99",
        "currency": "USD"
      },
      "inventory": [
        {
          "warehouse_id": "7555051682279851789",
          "quantity": 100
        }
      ],
      "sales_attributes": []
    }
  ],
  "package_weight": {
    "value": "60",
    "unit": "GRAM"
  },
  "package_dimensions": {
    "length": "1",
    "width": "20",
    "height": "25",
    "unit": "CENTIMETER"
  },
  "delivery_option_ids": ["SEND_BY_SELLER"],
  "is_cod_allowed": false,
  "product_certifications": []
}
```

#### 响应示例

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "product_id": "1732360802061029859",
    "skus": [
      {
        "id": "sku_id_123",
        "seller_sku": "STICKER-001"
      }
    ],
    "warnings": []
  }
}
```

---

### 2. 查询商品列表 (List Products)

**接口**: `POST /product/202309/products/search`

**功能**: 分页查询店铺商品列表

#### 请求示例

```python
async def list_products(
    page_size: int = 20, 
    page_token: str = "", 
    status: str | None = None
) -> dict[str, Any]:
    query = {"page_size": min(page_size, 100)}
    if page_token:
        query["page_token"] = page_token
    
    body = {}
    if status:
        body["filter"] = {"product_status": [status]}
    
    return await api_request(
        "POST",
        f"{_PRODUCT_PREFIX}/products/search",
        body=body,
        query_params=query,
    )
```

#### 商品状态 (status)

- `DRAFT`: 草稿
- `PENDING`: 审核中
- `FAILED`: 审核失败
- `LIVE`: 已上架
- `SELLER_DEACTIVATED`: 卖家下架
- `PLATFORM_DEACTIVATED`: 平台下架
- `FREEZE`: 冻结
- `DELETED`: 已删除

#### 响应示例

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "products": [
      {
        "id": "1732360802061029859",
        "title": "Cute Cat Stickers",
        "status": "LIVE",
        "create_time": 1704067200,
        "update_time": 1704067200
      }
    ],
    "total_count": 50,
    "next_page_token": "eyJvZmZzZXQiOjIwfQ=="
  }
}
```

---

### 3. 查询商品详情 (Get Product)

**接口**: `GET /product/202309/products/{product_id}`

**功能**: 获取单个商品的完整信息

#### 请求示例

```python
async def get_product(product_id: str) -> dict[str, Any]:
    return await api_request("GET", f"{_PRODUCT_PREFIX}/products/{product_id}")
```

#### 响应示例

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "1732360802061029859",
    "title": "Cute Cat Stickers",
    "description": "<p>High quality vinyl stickers</p>",
    "category_id": "928016",
    "status": "LIVE",
    "main_images": [...],
    "skus": [...],
    "package_weight": {...},
    "package_dimensions": {...}
  }
}
```

---

### 4. 更新商品 (Update Product)

**接口**: `PUT /product/202309/products/{product_id}`

**功能**: 更新已存在的商品信息

#### 请求示例

```python
async def update_product(product_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return await api_request("PUT", f"{_PRODUCT_PREFIX}/products/{product_id}", body=data)
```

#### 注意事项

- 只需传递需要更新的字段
- 不能修改已上架商品的某些关键字段（如类目）
- 更新后可能需要重新审核

---

### 5. 删除商品 (Delete Product)

**接口**: `DELETE /product/202309/products/{product_id}`

**功能**: 删除商品（软删除，可恢复）

#### 请求示例

```python
async def delete_product(product_id: str) -> dict[str, Any]:
    return await api_request("DELETE", f"{_PRODUCT_PREFIX}/products/{product_id}")
```

---

### 6. 上架商品 (Activate Product)

**接口**: `POST /product/202309/products/{product_id}/activate`

**功能**: 激活/上架商品

#### 请求示例

```python
async def activate_product(product_id: str) -> dict[str, Any]:
    return await api_request("POST", f"{_PRODUCT_PREFIX}/products/{product_id}/activate")
```

#### ⚠️ 已知问题

目前此接口可能返回 `code: 40006` 错误，建议在 TikTok Shop 后台手动上架商品。

---

### 7. 下架商品 (Deactivate Product)

**接口**: `POST /product/202309/products/{product_id}/deactivate`

**功能**: 下架商品

#### 请求示例

```python
async def deactivate_product(product_id: str) -> dict[str, Any]:
    return await api_request("POST", f"{_PRODUCT_PREFIX}/products/{product_id}/deactivate")
```

---

### 8. 恢复商品 (Recover Product)

**接口**: `POST /product/202309/products/{product_id}/recover`

**功能**: 恢复已删除的商品

#### 请求示例

```python
async def recover_product(product_id: str) -> dict[str, Any]:
    return await api_request("POST", f"{_PRODUCT_PREFIX}/products/{product_id}/recover")
```

---

## 辅助接口

### 1. 上传图片 (Upload Image)

**接口**: `POST /product/202309/images/upload`

**功能**: 上传商品图片，获取 TikTok 图片 URI

#### 请求示例

```python
async def upload_image(image_url: str) -> dict[str, Any]:
    """通过 URL 上传商品图片到 TikTok."""
    import httpx
    
    # 1. 下载图片
    async with httpx.AsyncClient(timeout=30) as client:
        img_resp = await client.get(image_url)
        img_resp.raise_for_status()
        image_data = img_resp.content
        content_type = img_resp.headers.get('content-type', 'image/jpeg')
    
    # 2. 生成签名（注意：图片上传不需要 shop_cipher）
    path = f"{_PRODUCT_PREFIX}/images/upload"
    timestamp = str(int(time.time()))
    
    params = {
        "app_key": app_key,
        "timestamp": timestamp,
    }
    
    # body 为空字符串（使用 multipart/form-data）
    sign = _generate_sign(app_secret, path, params, body="")
    params["sign"] = sign
    params["access_token"] = access_token
    
    # 3. 使用 multipart/form-data 上传
    url = f"https://open-api.tiktokglobalshop.com{path}"
    headers = {"x-tts-access-token": access_token}
    files = {"data": ("image.jpg", image_data, content_type)}
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, params=params, headers=headers, files=files)
        data = resp.json()
    
    return data.get("data", {})
```

#### 响应示例

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "uri": "tiktok://image_uri_abc123",
    "url": "https://p16-oec-va.ibyteimg.com/...",
    "width": 800,
    "height": 800
  }
}
```

#### 注意事项

- ✅ 使用 `multipart/form-data` 格式上传二进制文件
- ✅ 字段名必须是 `data`
- ❌ 不需要 `shop_cipher` 参数
- 支持格式: JPG, PNG, WEBP
- 图片要求: 最小 330x330px，最大 5MB

---

### 2. 查询类目 (Get Categories)

**接口**: `GET /product/202309/categories`

**功能**: 获取商品类目列表

#### 请求示例

```python
async def get_categories(keyword: str = "", category_version: str = "v2") -> dict[str, Any]:
    body = {"category_version": category_version}
    if keyword:
        body["keyword"] = keyword
    return await api_request("GET", f"{_PRODUCT_PREFIX}/categories", query_params=body)
```

#### 常用贴纸类目

| 类目名称 | category_id | 使用比例 |
|---------|-------------|---------|
| Stickers | 928016 | 70% |
| Phone Stickers | 1126416 | 20% |
| Sprays, Confetti & Streamers | 600019 | 10% |

---

### 3. 查询类目规则 (Get Category Rules)

**接口**: `GET /product/202309/categories/{category_id}/rules`

**功能**: 获取指定类目的商品规则

#### 请求示例

```python
async def get_category_rules(category_id: str) -> dict[str, Any]:
    return await api_request("GET", f"{_PRODUCT_PREFIX}/categories/{category_id}/rules")
```

---

### 4. 查询类目属性 (Get Category Attributes)

**接口**: `GET /product/202309/categories/{category_id}/attributes`

**功能**: 获取指定类目的必需/可选属性

#### 请求示例

```python
async def get_category_attributes(category_id: str) -> dict[str, Any]:
    return await api_request("GET", f"{_PRODUCT_PREFIX}/categories/{category_id}/attributes")
```

---

### 5. 查询品牌 (Get Brands)

**接口**: `GET /product/202309/brands`

**功能**: 搜索品牌列表

#### 请求示例

```python
async def get_brands(
    category_id: str = "", 
    keyword: str = "", 
    page_size: int = 20
) -> dict[str, Any]:
    body = {"page_size": page_size}
    if category_id:
        body["category_id"] = category_id
    if keyword:
        body["brand_name"] = keyword
    return await api_request("GET", f"{_PRODUCT_PREFIX}/brands", query_params=body)
```

---

## 完整示例

### 创建贴纸商品完整流程

```python
import asyncio
from app.platforms.tiktok import products

async def create_sticker_product():
    # 1. 上传图片
    image_url = "https://example.com/sticker.jpg"
    image_result = await products.upload_image(image_url)
    image_uri = image_result["uri"]
    
    # 2. 准备商品数据
    product_data = {
        "title": "Cute Cat Vinyl Stickers - Waterproof Laptop Decals",
        "description": """
            <p><strong>Premium Quality Vinyl Stickers</strong></p>
            <ul>
                <li>Waterproof and durable</li>
                <li>Perfect for laptops, water bottles, notebooks</li>
                <li>Easy to apply and remove</li>
                <li>Vibrant colors that won't fade</li>
            </ul>
        """,
        "category_id": "928016",  # Stickers
        "category_version": "v2",
        "main_images": [{"uri": image_uri}],
        "skus": [
            {
                "seller_sku": "STICKER-CAT-001",
                "price": {
                    "amount": "16.99",
                    "currency": "USD"
                },
                "inventory": [
                    {
                        "warehouse_id": "7555051682279851789",
                        "quantity": 100
                    }
                ],
                "sales_attributes": []
            }
        ],
        "package_weight": {
            "value": "60",
            "unit": "GRAM"
        },
        "package_dimensions": {
            "length": "1",
            "width": "20",
            "height": "25",
            "unit": "CENTIMETER"
        },
        "delivery_option_ids": ["SEND_BY_SELLER"],
        "is_cod_allowed": False,
        "product_certifications": []
    }
    
    # 3. 创建商品
    result = await products.create_product(product_data)
    product_id = result["product_id"]
    print(f"✅ 商品创建成功! ID: {product_id}")
    
    # 4. 查询商品详情
    product_detail = await products.get_product(product_id)
    print(f"商品状态: {product_detail['status']}")
    
    # 5. 尝试上架（可能需要手动操作）
    try:
        await products.activate_product(product_id)
        print("✅ 商品已上架")
    except Exception as e:
        print(f"⚠️ 自动上架失败，请在后台手动上架: {e}")
    
    return product_id

# 运行
asyncio.run(create_sticker_product())
```

---

## 错误处理

### 常见错误码

| 错误码 | 说明 | 解决方案 |
|-------|------|---------|
| 0 | 成功 | - |
| 40001 | 签名错误 | 检查签名算法和参数顺序 |
| 40002 | 参数错误 | 检查必需字段是否完整 |
| 40003 | 权限不足 | 检查 access_token 权限范围 |
| 40004 | 请求过于频繁 | 降低请求频率，添加重试机制 |
| 40006 | 接口路径错误 | 检查 API 路径是否正确 |
| 50000 | 服务器错误 | 稍后重试 |

### 错误处理示例

```python
from app.core.exceptions import PlatformAPIError

try:
    result = await products.create_product(product_data)
except PlatformAPIError as e:
    print(f"平台: {e.platform}")
    print(f"错误: {e.message}")
    print(f"详情: {e.details}")
    
    # 根据错误类型处理
    if "40002" in e.details:
        print("参数错误，请检查商品数据")
    elif "40004" in e.details:
        print("请求过于频繁，等待后重试")
        await asyncio.sleep(5)
```

---

## 常见问题

### 1. 图片上传失败？

**问题**: 图片上传返回签名错误

**解决方案**:
- ✅ 使用 `multipart/form-data` 格式
- ✅ 字段名必须是 `data`
- ❌ 不要添加 `shop_cipher` 参数
- ❌ 签名时 body 参数为空字符串

### 2. 商品创建成功但无法上架？

**问题**: `activate_product` 返回 `code: 40006`

**解决方案**:
- 商品创建后在 TikTok Shop 后台手动上架
- 或等待 TikTok 修复此 API 问题

### 3. 如何选择正确的类目？

**建议**:
- 普通贴纸 → `928016` (Stickers)
- 手机贴纸 → `1126416` (Phone Stickers)
- 喷雾/五彩纸屑 → `600019` (Sprays, Confetti & Streamers)

### 4. SKU 编码规则？

**建议格式**: `STICKER-{主题}-{编号}`

示例:
- `STICKER-CAT-001`
- `STICKER-FLOWER-002`
- `STICKER-ANIME-003`

### 5. 商品描述支持哪些 HTML 标签？

**支持的标签**:
- `<p>`, `<br>`, `<strong>`, `<em>`
- `<ul>`, `<ol>`, `<li>`
- `<h1>` - `<h6>`

**不支持**: `<script>`, `<style>`, `<iframe>` 等

### 6. 如何批量创建商品？

参考 `batch_sticker_generator.py` 和 `ai_sticker_generator.py`，使用 AI 自动生成系统。

### 7. search_keywords 字段如何使用？

**注意**: `search_keywords` 字段仅在 TikTok Shop 后台界面可用，API 不支持此字段。

**建议**: 将关键词融入商品标题和描述中以提高搜索排名。

---

## 相关文档

- [TikTok Shop 贴纸字段完整说明](./TIKTOK_STICKER_FIELDS.md)
- [TikTok Shop API 字段指南](./TIKTOK_STICKER_API_GUIDE.md)
- [AI 自动生成系统使用指南](./AI_GENERATOR_GUIDE.md)
- [字段配置参考](./sticker_field_config.py)

---

## 更新日志

- **2024-01**: 初始版本
- **2024-01**: 修复图片上传 API（使用 multipart/form-data）
- **2024-01**: 添加商品上架已知问题说明
- **2024-01**: 完善错误处理和常见问题

---

**文档维护**: 本文档基于 TikTok Shop Open API v2 (202309) 编写，如有更新请参考官方文档。
