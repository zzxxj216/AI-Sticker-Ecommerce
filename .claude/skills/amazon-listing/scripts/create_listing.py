"""创建 / 校验 Amazon listing(单品或变体族子/父)。

payload.json 结构:
  {"sku":"INK-...", "product_type":"LABEL", "attributes":{...}}
图片字段(main_product_image_locator / other_product_image_locator_N)的 media_location
可写成 "file:C:\\path\\img.png" —— 脚本会自动传 COS 换成公网 URL(内容哈希 key)。

用法:
  python create_listing.py validate payload.json     # VALIDATION_PREVIEW 零写入校验
  python create_listing.py create   payload.json     # 先 GET 防覆盖,再真建/更新

安全红线:只写 store=main(inkelligent);create 前 GET 确认不存在(变体子体除外,
见下);品牌一律 Inkelligent(GTIN 豁免)。
"""
import sys, json
import _amz


def _materialize_images(attrs: dict, sku: str):
    """把 attributes 里 media_location 以 'file:' 开头的本地图传 COS,替换为 URL。"""
    for k in list(attrs.keys()):
        if "image_locator" not in k:
            continue
        for item in attrs[k]:
            loc = item.get("media_location", "")
            if isinstance(loc, str) and loc.startswith("file:"):
                path = loc[5:]
                url = _amz.upload_image_cos(path, key_prefix=f"amazon/{sku.lower()}")
                item["media_location"] = url
                print(f"  COS: {path.split(chr(92))[-1]} -> {url.split('/')[-1]}")


def _load(p: str) -> dict:
    return json.loads(open(p, encoding="utf-8").read())


def run(action: str, payload_path: str):
    pl = _load(payload_path)
    sku = pl["sku"]; ptype = pl["product_type"]; attrs = pl["attributes"]
    _materialize_images(attrs, sku)

    if action == "validate":
        resp = _amz.put_listing(sku, ptype, attrs, mode="VALIDATION_PREVIEW")
        status, issues = _amz.issues_of(resp)
        print(f"[VALIDATE] {sku} status={status} issues={len(issues)}")
        for i in issues:
            print(f"   [{i.get('severity')}] {','.join(i.get('attributeNames') or [])} :: {i.get('message','')[:120]}")
        return

    if action == "create":
        # 父体可缺 offer;子体带 parentage_level=child。普通单品先 GET 防覆盖。
        is_child = any(a.get("value") == "child" for a in attrs.get("parentage_level", []))
        if not is_child and _amz.sku_exists(sku):
            print(f"[ABORT] {sku} 已存在,按红线拒绝覆盖(只增不改)。")
            return
        resp = _amz.put_listing(sku, ptype, attrs)
        status, issues = _amz.issues_of(resp)
        errs = [i for i in issues if i.get("severity") == "ERROR"]
        print(f"[CREATE] {sku} status={status} errors={len(errs)}")
        for i in errs:
            print(f"   ERR {','.join(i.get('attributeNames') or [])} :: {i.get('message','')[:120]}")
        if status in ("ACCEPTED", "VALID") and not errs:
            print(f"   OK -> submissionId={resp.get('data',{}).get('submissionId','')}")


if __name__ == "__main__":
    argv = _amz.consume_store(sys.argv[1:])
    if len(argv) < 2 or argv[0] not in ("validate", "create"):
        print(__doc__); sys.exit(1)
    run(argv[0], argv[1])
