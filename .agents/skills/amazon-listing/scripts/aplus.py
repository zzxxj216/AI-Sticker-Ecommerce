"""Amazon A+ 内容:列出 / 绑定到 ASIN / 用图建标准 A+。**纯走中间层 HTTP**(不再 import SDK)。

依赖:中间层在跑(env AMAZON_MCA_URL,默认 :8000)+ 已加 A+ 端点;create 时本地图先传 COS。

用法:
  python aplus.py list                                       # 列出所有 A+(名/状态/badge/key)
  python aplus.py bind <KEY> <ASIN> [<ASIN>...] [--no-submit]    # 关联 A+ 到 ASIN(默认提审)
  python aplus.py create "<名称>" <ASIN> <img1.png> [img2...]   # 本地图→COS→建标准A+→关联→提审
"""
import sys
import _amz


def cmd_list():
    o = _amz.api("GET", "/aplus/documents", params={"store": _amz.STORE})
    if not o.get("success"):
        print("FAIL:", o.get("message") or o.get("detail")); return
    for d in o.get("data", []):
        print("%-46s %-9s %-12s %s" % ((d.get("name") or "")[:46], d.get("status"),
                                       ",".join(d.get("badgeSet") or []), d.get("contentReferenceKey")))


def cmd_bind(key, asins, submit):
    o = _amz.api("POST", f"/aplus/documents/{key}/asins",
                 params={"store": _amz.STORE}, body={"asins": asins, "submit": submit})
    print(o.get("data") if o.get("success") else ("FAIL: " + str(o.get("message") or o.get("detail"))))


def cmd_create(name, asin, images, submit=True):
    # 本地图先传 COS(内容哈希 key),拿公网 URL 给中间层
    urls = []
    for f in images[:7]:
        if f.startswith(("http://", "https://")):
            urls.append(f)
        else:
            url = _amz.upload_image_cos(f, key_prefix="amazon/aplus")
            print("  COS:", url.split("/")[-1]); urls.append(url)
    o = _amz.api("POST", "/aplus/documents", params={"store": _amz.STORE},
                 body={"name": name, "asin": asin, "image_urls": urls, "submit": submit}, timeout=300)
    print(o.get("data") if o.get("success") else ("FAIL: " + str(o.get("message") or o.get("detail"))))


if __name__ == "__main__":
    a = _amz.consume_store(sys.argv[1:])
    if not a:
        print(__doc__); sys.exit(1)
    if a[0] == "list":
        cmd_list()
    elif a[0] == "bind":
        submit = "--no-submit" not in a
        rest = [x for x in a[1:] if not x.startswith("--")]
        cmd_bind(rest[0], rest[1:], submit)
    elif a[0] == "create":
        cmd_create(a[1], a[2], list(a[3:]))
    else:
        print(__doc__)
