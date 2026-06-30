"""腾讯云 COS 图床 —— 把本地图上传到公网,供 Amazon 服务端抓取。

为什么需要:Amazon 上品/校验阶段会**真实抓取**图片 URL(localhost/占位图会 InvalidInput),
所以主副图 + A+ 模块图必须先传到公网可达的对象存储,再把 URL 写进 listing 的
``main_product_image_locator`` 等字段。详见 docs/amazon_custom_listing_design.md 图片层。

跨境抓图:COS 有海外地域(如 na-ashburn=美国东部、na-siliconvalley=美国西部)。
**建议把存图的桶建在美国地域**,Amazon 美国抓图最快最稳;桶须为**公有读**。

配置(.env):
    COS_SECRET_ID    腾讯云 SecretId
    COS_SECRET_KEY   腾讯云 SecretKey
    COS_REGION       地域,如 na-ashburn / ap-guangzhou
    COS_BUCKET       桶名(含 APPID),如 amazon-img-1250000000
    COS_BASE_URL     公网基址(含协议),留空则用 https://<bucket>.cos.<region>.myqcloud.com
                     (绑 CDN 加速域名时填这里)

对外接口与 provider 无关(upload_bytes/upload_file/public_url/verify_reachable/is_configured),
日后换图床只改本文件。依赖:cos-python-sdk-v5。
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from typing import Optional

import requests

COS_SECRET_ID = os.getenv("COS_SECRET_ID", "")
COS_SECRET_KEY = os.getenv("COS_SECRET_KEY", "")
COS_REGION = os.getenv("COS_REGION", "")
COS_BUCKET = os.getenv("COS_BUCKET", "")
COS_BASE_URL = os.getenv("COS_BASE_URL", "").rstrip("/")


class CosCdn:
    """最小腾讯 COS 上传器:上传字节/文件 → 返回公网 URL。

    幂等:相同 key 覆盖同一对象(同一张图重传不产生新对象)。
    """

    def __init__(
        self,
        *,
        secret_id: str = COS_SECRET_ID,
        secret_key: str = COS_SECRET_KEY,
        region: str = COS_REGION,
        bucket: str = COS_BUCKET,
        base_url: str = COS_BASE_URL,
    ) -> None:
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.region = region
        self.bucket = bucket
        # 公共基址缺省按 COS 默认访问域名推断
        self.base_url = (
            base_url.rstrip("/")
            or (f"https://{bucket}.cos.{region}.myqcloud.com" if bucket and region else "")
        )
        self._client = None  # 懒加载

    # ---- 配置探测 ----
    def is_configured(self) -> bool:
        return all([self.secret_id, self.secret_key, self.region, self.bucket, self.base_url])

    def _require(self) -> None:
        if not self.is_configured():
            missing = [n for n, v in [
                ("COS_SECRET_ID", self.secret_id),
                ("COS_SECRET_KEY", self.secret_key),
                ("COS_REGION", self.region),
                ("COS_BUCKET", self.bucket),
                ("COS_BASE_URL", self.base_url),
            ] if not v]
            raise RuntimeError("腾讯 COS 图床未配置,缺少: %s" % ", ".join(missing))

    @property
    def client(self):
        if self._client is None:
            self._require()
            from qcloud_cos import CosConfig, CosS3Client  # 延迟导入
            config = CosConfig(
                Region=self.region,
                SecretId=self.secret_id,
                SecretKey=self.secret_key,
                Scheme="https",
            )
            self._client = CosS3Client(config)
        return self._client

    # ---- URL 拼装 ----
    def public_url(self, key: str) -> str:
        return f"{self.base_url}/{key.lstrip('/')}"

    # ---- 上传 ----
    def upload_bytes(
        self,
        data: bytes,
        key: str,
        *,
        content_type: Optional[str] = None,
    ) -> str:
        """上传字节到 ``key``,返回公网 URL。content_type 缺省按 key 后缀推断。"""
        self._require()
        key = key.lstrip("/")
        ctype = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.client.put_object(
            Bucket=self.bucket, Body=data, Key=key, ContentType=ctype,
        )
        return self.public_url(key)

    def upload_file(self, path: str, key: Optional[str] = None) -> str:
        """上传本地文件,返回公网 URL。

        key 缺省用 ``amazon/<sha1前12>.<ext>`` —— 内容寻址,相同图天然去重。
        """
        with open(path, "rb") as f:
            data = f.read()
        if not key:
            ext = os.path.splitext(path)[1].lstrip(".").lower() or "bin"
            digest = hashlib.sha1(data).hexdigest()[:12]
            key = f"amazon/{digest}.{ext}"
        return self.upload_bytes(data, key)

    # ---- 校验公网可达(写进 listing 前自检,避免 Amazon 抓图 InvalidInput) ----
    @staticmethod
    def verify_reachable(url: str, timeout: int = 15) -> bool:
        """HEAD/GET 探测 URL 是否公网可达且像图片。失败返回 False(不抛)。"""
        try:
            r = requests.head(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 405 or "image" not in (r.headers.get("Content-Type") or ""):
                r = requests.get(url, timeout=timeout, stream=True)
            ok = 200 <= r.status_code < 300
            ctype = r.headers.get("Content-Type") or ""
            return ok and ("image" in ctype or ctype == "")
        except requests.RequestException:
            return False


_cdn: Optional[CosCdn] = None


def get_cdn() -> CosCdn:
    """进程级单例。"""
    global _cdn
    if _cdn is None:
        _cdn = CosCdn()
    return _cdn
