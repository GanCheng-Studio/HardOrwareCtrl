"""协议注册表 + 官网链接解析。

- protocols.json：协议定义 + 已知型号关键词库（用户可自行编辑扩充）。
- 通过粘贴官网产品页链接，抓取页面标题/元信息，匹配关键词得到协议。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE  # 部分官网证书链不完整

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG_PATH = os.path.join(_HERE, "protocols.json")


def _registry() -> dict:
    try:
        with open(REG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"protocols": [], "models": []}


def list_protocols() -> list[dict]:
    return _registry().get("protocols", [])


def protocol_name(pid: str) -> str:
    for p in list_protocols():
        if p["id"] == pid:
            return p["name"]
    return pid or "未知协议"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (s or "").lower())


def fetch_page(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
        raw = resp.read()
    try:
        head = raw[:4096].decode("ascii", errors="ignore")
        m = re.search(r'charset=["\']?([\w-]+)', head, re.I)
        enc = m.group(1) if m else "utf-8"
    except Exception:
        enc = "utf-8"
    return raw.decode(enc, errors="ignore")


def extract_info(html: str) -> dict:
    """从 HTML 中提取产品名/型号线索。"""
    info = {"title": "", "site": "", "text": ""}
    texts: list[str] = []

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        info["title"] = t
        texts.append(t)

    for prop in ("og:title", "og:site_name", "og:product:name"):
        m = re.search(r'<meta[^>]+property=["\']%s["\'][^>]*content=["\'](.*?)["\']' % prop,
                      html, re.I | re.S)
        if m:
            v = m.group(1).strip()
            if prop == "og:site_name":
                info["site"] = v
            else:
                texts.append(v)

    m = re.search(r'<meta[^>]+name=["\'](?:description|keywords)["\'][^>]*content=["\'](.*?)["\']',
                  html, re.I | re.S)
    if m:
        texts.append(m.group(1))

    # JSON-LD 商品名
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        for item in stack:
            if isinstance(item, dict) and item.get("@type") in ("Product", "Offer", "ItemList"):
                name = item.get("name")
                if name:
                    texts.append(str(name))

    info["text"] = " ".join(texts)
    return info


def match_model(info: dict) -> dict | None:
    """用关键词库匹配型号，返回最佳匹配或 None。"""
    hay = _norm(info.get("text", ""))
    if not hay:
        return None
    best, best_score = None, 0
    for entry in _registry().get("models", []):
        score = 0
        for kw in entry.get("keywords", []):
            k = _norm(kw)
            if k and k in hay:
                score += len(k)
        if score > best_score:
            best, best_score = entry, score
    return best


def resolve_link(url: str) -> dict:
    """抓取链接并返回 {title, site, model, brand, protocol, note, error}。"""
    result = {"url": url, "title": "", "site": "", "model": "", "brand": "",
              "protocol": "gatt_battery", "note": "", "error": ""}
    try:
        html = fetch_page(url)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"链接抓取失败：{e}"
        return result
    info = extract_info(html)
    result["title"] = info.get("title", "")
    result["site"] = info.get("site", "")
    entry = match_model(info)
    if entry:
        result["model"] = entry.get("model", "")
        result["brand"] = entry.get("brand", "")
        result["protocol"] = entry.get("protocol", "gatt_battery")
        result["note"] = entry.get("note", "")
    else:
        result["note"] = "未在关键词库中匹配到已知型号，默认使用标准 GATT 电量协议。"
    return result
