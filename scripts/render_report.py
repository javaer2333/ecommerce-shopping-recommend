#!/usr/bin/env python3
"""Render an e-commerce recommendation report (Markdown + standalone HTML).

Input : products.json produced by the ecommerce-shopping-recommend skill.
Output: report.md, report.html and a copy of products.json in --outdir.

Only the Python standard library is used, so the skill works on a bare macOS
or Linux box without pip installs.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TIER_STRONG = ("强烈推荐", "strong")
TIER_REC = ("推荐", "rec")
TIER_CONSIDER = ("可考虑", "consider")
TIER_CAUTION = ("谨慎", "caution")
TIER_NO = ("不推荐", "no")

FOOD_WORDS = ("食品", "零食", "坚果", "饮料", "乳品", "生鲜", "粮油", "调味", "母婴", "辅食", "酒")
SERIOUS_WORDS = ("假货", "假", "过期", "临期", "翻新", "货不对板", "异味", "过敏", "变质", "三无")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def as_int(value: Any) -> Optional[int]:
    number = as_float(value)
    return int(round(number)) if number is not None else None


def parse_sales_num(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).replace(",", "").replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(亿|万|w|W|千|k|K)?", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or ""
    if unit == "亿":
        number *= 100_000_000
    elif unit in ("万", "w", "W"):
        number *= 10_000
    elif unit in ("千", "k", "K"):
        number *= 1_000
    return int(round(number))


def money(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "未获取"
    if abs(number - round(number)) < 0.001:
        return "¥{:,.0f}".format(number)
    return "¥{:,.2f}".format(number)


def fmt_pct(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "未获取"
    if abs(number - round(number)) < 0.001:
        return "{}%".format(int(round(number)))
    return "{:.1f}%".format(number)


def fmt_num(value: Any) -> str:
    number = as_int(value)
    return "未获取" if number is None else "{:,}".format(number)


def text(value: Any, default: str = "未获取") -> str:
    if value is None or value == "" or value == [] or value == {}:
        return default
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) or default
    if isinstance(value, dict):
        return "；".join("{}：{}".format(k, v) for k, v in value.items()) or default
    return str(value)


def safe_url(value: Any) -> str:
    url = str(value or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    return ""


def esc(value: Any) -> str:
    return html.escape(text(value, default=""), quote=True)


def md_cell(value: Any) -> str:
    raw = text(value)
    return raw.replace("|", "\\|").replace("\n", "<br>").strip()


def md_link(label: Any, url: Any) -> str:
    safe = safe_url(url)
    clean_label = str(label or "查看").replace("]", "\\]")
    return "[{}](<{}>)".format(clean_label, safe) if safe else clean_label


def tier_for(rate: Any) -> Tuple[str, str]:
    value = as_float(rate)
    if value is None:
        return TIER_CONSIDER
    if value >= 90:
        return TIER_STRONG
    if value >= 80:
        return TIER_REC
    if value >= 70:
        return TIER_CONSIDER
    if value >= 60:
        return TIER_CAUTION
    return TIER_NO


def platform_class(platform: Any) -> str:
    name = str(platform or "")
    if "京东" in name:
        return "jd"
    if "淘宝" in name or "天猫" in name:
        return "taobao"
    if "拼多多" in name:
        return "pdd"
    if "抖音" in name:
        return "douyin"
    if "闲鱼" in name:
        return "xianyu"
    return "other"


def is_food(product: Dict[str, Any]) -> bool:
    if product.get("nutrition"):
        return True
    category = "{} {}".format(product.get("category") or "", product.get("name") or "")
    return any(word in category for word in FOOD_WORDS)


def last_captured(product: Dict[str, Any], fallback: str) -> str:
    stamps = [
        str(item.get("captured_at"))
        for item in (product.get("evidence") or [])
        if isinstance(item, dict) and item.get("captured_at")
    ]
    return max(stamps) if stamps else fallback


def filled(value: Any) -> bool:
    return value not in (None, "", [], {}, ())


# --------------------------------------------------------------------------- #
# scoring (fills only missing values, explicit values win)
# --------------------------------------------------------------------------- #

def median_price(products: List[Dict[str, Any]]) -> Optional[float]:
    prices = [as_float(p.get("price")) for p in products]
    valid = [price for price in prices if price is not None and price > 0]
    return statistics.median(valid) if valid else None


def quality_score(product: Dict[str, Any]) -> float:
    positive = as_float(product.get("positive_rate"))
    rating = as_float(product.get("rating"))
    reviews = as_int(product.get("review_count")) or 0
    if rating is not None and rating > 5:
        rating = rating / 20.0
    confidence = clamp(math.log10(reviews + 1) / 5 * 100) if reviews > 0 else 0.0

    if positive is not None:
        pr_score = clamp((positive - 90) * 10)
        if rating is not None:
            rate_score = clamp((rating - 4.0) / 0.9 * 100)
            return 0.70 * pr_score + 0.20 * rate_score + 0.10 * confidence
        return 0.80 * pr_score + 0.20 * confidence
    if rating is not None:
        rate_score = clamp((rating - 4.0) / 0.9 * 100)
        return 0.65 * rate_score + 0.35 * confidence
    return 50.0


def sales_score(product: Dict[str, Any]) -> float:
    sales = as_int(product.get("sales_num"))
    if not sales:
        sales = parse_sales_num(product.get("sales")) or 0
    return clamp(math.log10(sales + 1) / 5 * 100)


def price_score(product: Dict[str, Any], median: Optional[float]) -> float:
    price = as_float(product.get("price"))
    if median is None or not price or price <= 0:
        return 50.0
    ratio = price / median
    if ratio <= 1:
        score = 100 - 15 * (1 - ratio)
    else:
        score = 100 - 55 * math.log(ratio)
    return clamp(score)


def trust_score(product: Dict[str, Any]) -> float:
    platform = str(product.get("platform") or "")
    shop_type = "{} {}".format(product.get("shop_type") or "", product.get("shop") or "")

    if "闲鱼" in platform:
        base = 60.0
        credit = as_int(product.get("credit_score") or product.get("zhima_credit"))
        if credit is not None:
            if credit >= 750:
                base += 20
            elif credit >= 700:
                base += 12
            elif credit >= 650:
                base += 5
            else:
                base -= 20
        if product.get("verified"):
            base += 5
        if product.get("inspection"):
            base += 5
    elif any(word in shop_type for word in ("自营", "官方旗舰", "旗舰", "百亿补贴")):
        base = 90.0
    elif "专营" in shop_type:
        base = 80.0
    elif "企业" in shop_type or "授权" in shop_type:
        base = 70.0
    elif "个人" in shop_type:
        base = 55.0
    else:
        base = 75.0

    shop_rating = as_float(product.get("shop_rating"))
    if shop_rating is not None:
        if shop_rating > 5:
            shop_rating = shop_rating / 20.0
        base += max(-10.0, min(10.0, (shop_rating - 4.6) * 20))
    return clamp(base)


def info_score(product: Dict[str, Any]) -> float:
    required = ["details", "specs", "after_sales", "url", "evidence"]
    if is_food(product):
        required += ["nutrition", "ingredients", "shelf_life"]
    done = sum(1 for key in required if filled(product.get(key)))
    return clamp(40 + 60 * done / len(required), 40, 100)


def risk_penalty(product: Dict[str, Any]) -> float:
    tags = product.get("risk_tags") or []
    bad = product.get("bad_review_keywords") or []
    serious = sum(1 for item in bad if any(word in str(item) for word in SERIOUS_WORDS))
    missing = sum(
        1
        for key in ("price", "positive_rate", "sales_num")
        if product.get(key) in (None, "")
    )
    return min(15.0, 3 * len(tags) + 2 * serious + 3 * missing)


def fill_scores(products: List[Dict[str, Any]]) -> None:
    median = median_price(products)
    for product in products:
        scores = product.get("scores")
        if not isinstance(scores, dict):
            scores = {}
            product["scores"] = scores

        defaults = {
            "quality": quality_score(product),
            "sales": sales_score(product),
            "price": price_score(product, median),
            "trust": trust_score(product),
            "info": info_score(product),
            "risk_penalty": risk_penalty(product),
        }
        for key, value in defaults.items():
            if scores.get(key) is None:
                scores[key] = round(value, 1)

        if scores.get("total") is None:
            total = (
                0.35 * as_float(scores["quality"])
                + 0.20 * as_float(scores["sales"])
                + 0.20 * as_float(scores["price"])
                + 0.15 * as_float(scores["trust"])
                + 0.10 * as_float(scores["info"])
            )
            scores["total"] = round(total, 1)
        if scores.get("ai_rate") is None:
            penalty = as_float(scores.get("risk_penalty")) or 0
            scores["ai_rate"] = int(round(clamp(as_float(scores["total"]) - penalty)))


def normalize(data: Dict[str, Any]) -> None:
    meta = data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        data["meta"] = meta
    meta.setdefault("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
    meta.setdefault("title", "{} 选购推荐报告".format(meta.get("query") or "商品"))

    products = data.get("products")
    if not isinstance(products, list):
        products = []
        data["products"] = products
    products = [item for item in products if isinstance(item, dict)]
    data["products"] = products

    for product in products:
        if not isinstance(product, dict):
            continue
        if product.get("sales_num") is None:
            product["sales_num"] = parse_sales_num(product.get("sales"))
        product.setdefault("risk_tags", [])
        product.setdefault("bad_review_keywords", [])
        product.setdefault("pros", [])
        product.setdefault("cons", [])
        product.setdefault("evidence", [])

    fill_scores([p for p in products if isinstance(p, dict)])

    products.sort(key=lambda item: as_float(item.get("scores", {}).get("ai_rate")) or 0, reverse=True)
    for index, product in enumerate(products, start=1):
        product["rank"] = index


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

def render_markdown(data: Dict[str, Any]) -> str:
    meta = data.get("meta") or {}
    products = data.get("products") or []
    excluded = data.get("excluded") or []
    conclusion = data.get("conclusion") or {}
    generated = meta.get("generated_at") or ""
    platforms = text(meta.get("platforms"), default="未指定")
    title = meta.get("title") or "选购推荐报告"

    lines: List[str] = []
    lines.append("# {}".format(title))
    lines.append("")
    lines.append(
        "> 查询对象：{} ｜ 输入类型：{} ｜ 平台：{} ｜ 预算：{}".format(
            text(meta.get("query")), text(meta.get("input_type")), platforms, text(meta.get("budget"))
        )
    )
    lines.append("> 生成时间：{} ｜ 分析：{}".format(generated, text(meta.get("analyst"), "DSH")))
    if meta.get("notes"):
        lines.append("> 备注：{}".format(text(meta.get("notes"))))
    lines.append("")

    summary = meta.get("summary") or data.get("summary") or ""
    if not summary and products:
        top = products[0]
        summary = "综合首选：{}（{}），AI 推荐率 {}/100。".format(
            text(top.get("name")), text(top.get("platform")), text(top.get("scores", {}).get("ai_rate"))
        )
    lines.append("## 结论速览")
    lines.append("")
    lines.append(summary or "本次样本不足，未能形成明确结论。")
    lines.append("")

    lines.append("| 排名 | 商品 | 平台 | 到手价 | 好评率 | 销量 | AI推荐率 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for product in products:
        scores = product.get("scores") or {}
        rate = scores.get("ai_rate")
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                product.get("rank"),
                md_link(product.get("name"), product.get("url")),
                md_cell(product.get("platform")),
                money(product.get("price")),
                fmt_pct(product.get("positive_rate")),
                md_cell(product.get("sales")),
                "{}（{}）".format(text(rate), tier_for(rate)[0]),
            )
        )
    lines.append("")

    if conclusion:
        lines.append("**场景建议**")
        lines.append("")
        labels = [
            ("best_value", "性价比首选"),
            ("best_quality", "品质/售后首选"),
            ("budget_pick", "预算有限"),
            ("avoid", "建议避开"),
        ]
        for key, label in labels:
            if conclusion.get(key):
                lines.append("- **{}**：{}".format(label, text(conclusion[key])))
        lines.append("")

    lines.append("## 推荐商品详情")
    lines.append("")
    for product in products:
        lines.extend(markdown_product(product, generated))

    lines.append("## 横向对比")
    lines.append("")
    lines.append("| 商品 | 平台 | 到手价 | 好评率 | 销量 | 评分 | 店铺 | AI推荐率 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for product in products:
        scores = product.get("scores") or {}
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                md_cell(product.get("name")),
                md_cell(product.get("platform")),
                money(product.get("price")),
                fmt_pct(product.get("positive_rate")),
                md_cell(product.get("sales")),
                text(product.get("rating")),
                md_cell(product.get("shop")),
                text(scores.get("ai_rate")),
            )
        )
    lines.append("")

    lines.append("## 已排除商品")
    lines.append("")
    if excluded:
        lines.append("| 商品 | 平台 | 排除原因 |")
        lines.append("| --- | --- | --- |")
        for item in excluded:
            if isinstance(item, dict):
                lines.append(
                    "| {} | {} | {} |".format(
                        md_cell(item.get("name")), md_cell(item.get("platform")), md_cell(item.get("reason"))
                    )
                )
            else:
                lines.append("| {} | - | {} |".format(md_cell(item), "不符合推荐阈值"))
        lines.append("")
    else:
        lines.append("本次没有商品触发硬性排除规则。")
        lines.append("")

    lines.append("## 数据说明与免责声明")
    lines.append("")
    lines.append(
        text(
            data.get("methodology"),
            "评分维度：口碑 35%、销量 20%、价格 20%、店铺信任 15%、信息完整度 10%，再扣除风险分。",
        )
    )
    lines.append("")
    lines.append(
        "- **AI 推荐率**是本报告模型的综合评分（0–100），不是平台官方指标，也不是购买概率。"
    )
    lines.append("- 好评率/销量为平台页面展示口径；标注「推算」的字段由评分、评价数与差评情感推得。")
    lines.append("- 价格、优惠、库存会变动，下单前请以平台实时页面为准。")
    lines.append("- 本报告不含推广返利链接；商品版权与图片归原平台/商家所有。")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def markdown_product(product: Dict[str, Any], generated: str) -> List[str]:
    scores = product.get("scores") or {}
    rate = scores.get("ai_rate")
    tier, _ = tier_for(rate)
    captured = last_captured(product, generated)
    platform = text(product.get("platform"))
    name = text(product.get("name"))

    lines: List[str] = []
    lines.append("### {}. {}".format(product.get("rank"), name))
    lines.append("")
    lines.append(
        "{} ｜ 平台：{} ｜ 店铺：{} ｜ AI 推荐率：**{}/100（{}）**".format(
            md_link("查看商品", product.get("url")), platform, text(product.get("shop")), text(rate), tier
        )
    )
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("| --- | --- |")
    price_line = money(product.get("price"))
    if as_float(product.get("original_price")):
        price_line += "（原价 {}）".format(money(product.get("original_price")))
    rows = [
        ("到手价", price_line),
        ("单位价格", text(product.get("unit_price"))),
        ("好评率", fmt_pct(product.get("positive_rate"))),
        ("销量", text(product.get("sales"))),
        ("评价数", fmt_num(product.get("review_count"))),
        ("商品评分", text(product.get("rating"))),
        ("店铺评分", text(product.get("shop_rating"))),
        ("店铺类型", text(product.get("shop_type"))),
        ("售后", text(product.get("after_sales"))),
    ]
    for key, value in rows:
        lines.append("| {} | {} |".format(key, md_cell(value)))
    lines.append("")

    if product.get("details"):
        lines.append("**详情**：{}".format(text(product.get("details"))))
        lines.append("")
    if product.get("specs"):
        specs = product["specs"]
        if isinstance(specs, dict):
            lines.append("**规格/参数**：{}".format("；".join("{}：{}".format(k, v) for k, v in specs.items())))
        else:
            lines.append("**规格/参数**：{}".format(text(specs)))
        lines.append("")

    if is_food(product) and product.get("nutrition"):
        basis = product.get("nutrition_basis") or "每 100g"
        lines.append("**营养成分表（{}）**".format(basis))
        lines.append("")
        lines.append("| 项目 | 含量 | NRV% |")
        lines.append("| --- | --- | --- |")
        for row in product["nutrition"]:
            if isinstance(row, dict):
                lines.append(
                    "| {} | {} | {} |".format(
                        md_cell(row.get("item")), md_cell(row.get("per_100g")), md_cell(row.get("nrv", "-"))
                    )
                )
        lines.append("")
        if product.get("ingredients"):
            lines.append("**配料表**：{}".format(text(product.get("ingredients"))))
            lines.append("")
        if product.get("shelf_life"):
            lines.append("**保质期/储存**：{}".format(text(product.get("shelf_life"))))
            lines.append("")

    if product.get("pros") or product.get("cons"):
        if product.get("pros"):
            lines.append("**优点**")
            lines.append("")
            for item in product["pros"]:
                lines.append("- {}".format(text(item)))
            lines.append("")
        if product.get("cons"):
            lines.append("**缺点/注意事项**")
            lines.append("")
            for item in product["cons"]:
                lines.append("- {}".format(text(item)))
            lines.append("")

    if product.get("vs_peers"):
        lines.append("**较同类产品的对比**：{}".format(text(product.get("vs_peers"))))
        lines.append("")
    if product.get("ai_reason"):
        lines.append("**AI 推荐说明**：{}".format(text(product.get("ai_reason"))))
        lines.append("")
    if product.get("risk_tags"):
        lines.append("**风险提示**：{}".format("、".join(str(item) for item in product["risk_tags"])))
        lines.append("")

    evidence = product.get("evidence") or []
    if evidence:
        lines.append("<details>")
        lines.append("<summary>数据来源</summary>")
        lines.append("")
        lines.append("| 字段 | 值 | 来源 | 采集时间 |")
        lines.append("| --- | --- | --- | --- |")
        for item in evidence:
            if not isinstance(item, dict):
                continue
            source = md_link(item.get("source") or "来源", item.get("url")) if item.get("url") else md_cell(item.get("source"))
            lines.append(
                "| {} | {} | {} | {} |".format(
                    md_cell(item.get("field")), md_cell(item.get("value")), source, md_cell(item.get("captured_at"))
                )
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append("来源：{} ｜ 采集时间：{}".format(md_link(platform + " 商品页", product.get("url")), captured))
    lines.append("")
    return lines


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

CSS = r"""
:root{
  --bg:#f4f6fb;--card:#ffffff;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;
  --accent:#2563eb;--accent-2:#7c3aed;--good:#059669;--warn:#d97706;--bad:#dc2626;
  --radius:18px;--shadow:0 10px 30px rgba(15,23,42,.07);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}
.hero{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 58%,#312e81 100%);color:#fff;padding:56px 0 42px}
.eyebrow{margin:0 0 10px;font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:#bfdbfe}
.hero h1{margin:0;font-size:clamp(26px,4vw,40px);line-height:1.25;letter-spacing:-.01em}
.hero .meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:999px;
  background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);font-size:12.5px;color:#e5e7eb}
.hero .summary{margin:20px 0 0;max-width:820px;font-size:16px;color:#e0e7ff}
.hero-stats{display:flex;gap:26px;margin-top:24px;flex-wrap:wrap}
.hero-stats b{display:block;font-size:24px;line-height:1.2}
.hero-stats span{font-size:12px;color:#c7d2fe}
main{padding:34px 0 64px}
section{margin-top:34px}
h2{font-size:22px;margin:0 0 16px;letter-spacing:-.01em}
h3{font-size:19px;margin:0;line-height:1.4}
h4{font-size:14px;margin:0 0 8px;color:#374151}
.muted{color:var(--muted)}
.rank-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.rank-card{display:flex;align-items:center;gap:12px;background:var(--card);border:1px solid var(--line);
  border-radius:14px;padding:12px 14px;color:inherit;transition:.18s ease;box-shadow:0 2px 8px rgba(15,23,42,.03)}
.rank-card:hover{transform:translateY(-2px);border-color:#bfdbfe;text-decoration:none;box-shadow:var(--shadow)}
.rank-no{width:30px;height:30px;border-radius:50%;background:#eff6ff;color:var(--accent);
  display:grid;place-items:center;font-weight:800;font-size:13px;flex:none}
.rank-card div{min-width:0;flex:1}
.rank-card strong{display:block;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rank-card small{color:var(--muted);font-size:12px}
.rank-score{font-weight:800;color:var(--accent);font-size:18px}
.product{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:24px;margin:18px 0;box-shadow:var(--shadow);scroll-margin-top:20px}
.product-head{display:flex;align-items:flex-start;gap:14px}
.product-head .titles{flex:1;min-width:0}
.product-head .sub{margin:6px 0 0;color:var(--muted);font-size:13px}
.score-wrap{display:flex;flex-direction:column;align-items:center;gap:4px;flex:none;min-width:84px}
.score-ring{width:72px;height:72px;border-radius:50%;display:grid;place-items:center;position:relative;
  background:conic-gradient(var(--ring,#2563eb) calc(var(--p)*1%),#e5e7eb 0)}
.score-ring::before{content:"";position:absolute;inset:7px;border-radius:50%;background:#fff}
.score-ring span{position:relative;font-size:20px;font-weight:800;color:#111827}
.score-wrap small{color:var(--muted);font-size:11px}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:700;line-height:1.7}
.badge.jd{background:#fee2e2;color:#b91c1c}
.badge.taobao{background:#ffedd5;color:#c2410c}
.badge.pdd{background:#fce7f3;color:#be185d}
.badge.douyin{background:#e5e7eb;color:#111827}
.badge.xianyu{background:#fef3c7;color:#92400e}
.badge.other{background:#ede9fe;color:#5b21b6}
.tier{font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:999px;white-space:nowrap}
.tier.strong{background:#dcfce7;color:#166534}
.tier.rec{background:#dbeafe;color:#1d4ed8}
.tier.consider{background:#fef9c3;color:#854d0e}
.tier.caution{background:#ffedd5;color:#9a3412}
.tier.no{background:#fee2e2;color:#991b1b}
.price-row{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:18px 0 14px}
.price{font-size:30px;font-weight:800;letter-spacing:-.02em;color:#0f172a}
.original{color:var(--muted);text-decoration:line-through;font-size:14px}
.unit{color:var(--muted);font-size:13px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:4px 0 18px}
.stat{background:#f8fafc;border:1px solid #eef2f7;border-radius:12px;padding:10px 12px}
.stat b{display:block;font-size:16px;line-height:1.35}
.stat span{font-size:11.5px;color:var(--muted)}
.details{margin:0 0 16px;color:#374151}
.specs{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}
.spec{background:#f1f5f9;border-radius:8px;padding:4px 9px;font-size:12.5px;color:#334155}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:16px 0}
.cols ul{margin:0;padding-left:18px}
.cols li{margin:4px 0}
.cols .cons li{color:#7c2d12}
.panel{background:#f8fafc;border:1px solid #eef2f7;border-radius:12px;padding:14px 16px;margin:14px 0}
.panel.scenario{background:linear-gradient(180deg,#f8fafc,#fff);border-color:#e2e8f0}
.scenario-list{margin:0;padding-left:18px}
.scenario-list li{margin:6px 0}
.panel.ai{background:#eff6ff;border-color:#dbeafe;border-left:4px solid var(--accent)}
.panel p{margin:0}
.risk{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0}
.risk span{background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;border-radius:999px;padding:3px 10px;font-size:12px}
.table-wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 2px 8px rgba(15,23,42,.03)}
.table-hint{display:none;margin:0 0 8px;color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:620px}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #eef2f7;vertical-align:top}
th{background:#f8fafc;color:#475569;font-weight:700;font-size:12.5px;white-space:nowrap}
tr:last-child td{border-bottom:0}
.nutrition table{min-width:420px}
.nutrition th{background:#f0fdf4;color:#166534}
details{margin-top:14px;border-top:1px dashed var(--line);padding-top:12px}
summary{cursor:pointer;color:var(--accent);font-size:13px;font-weight:600}
details table{margin-top:10px}
footer{border-top:1px solid var(--line);padding:24px 0 40px;color:var(--muted);font-size:12.5px}
footer p{margin:6px 0}
.empty{background:#fff;border:1px dashed var(--line);border-radius:14px;padding:22px;color:var(--muted)}
@media(max-width:720px){
  .hero{padding:38px 0 30px}
  .product{padding:18px}
  .product-head{flex-wrap:wrap}
  .score-wrap{margin-left:auto}
  .cols{grid-template-columns:1fr}
  .price{font-size:26px}
  table{min-width:560px}
  .table-hint{display:block}
}
@media print{
  body{background:#fff}
  .hero{background:#1e3a8a !important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .product,.table-wrap,.rank-card{box-shadow:none}
  .product{break-inside:avoid;page-break-inside:avoid}
  details{display:block}
}
"""


def badge(platform: Any) -> str:
    name = esc(platform)
    return '<span class="badge {}">{}</span>'.format(platform_class(platform), name)


def html_list(items: Any, empty: str = "未获取") -> str:
    if not items:
        return '<p class="muted">{}</p>'.format(esc(empty))
    values = items if isinstance(items, (list, tuple)) else [items]
    return "<ul>{}</ul>".format("".join("<li>{}</li>".format(esc(item)) for item in values))


def nutrition_html(product: Dict[str, Any]) -> str:
    rows = product.get("nutrition")
    if not is_food(product) or not rows:
        return ""
    basis = product.get("nutrition_basis") or "每 100g"
    body = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        body.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(row.get("item")), esc(row.get("per_100g")), esc(row.get("nrv", "-"))
            )
        )
    extra = []
    if product.get("ingredients"):
        extra.append("<p><strong>配料表：</strong>{}</p>".format(esc(product.get("ingredients"))))
    if product.get("shelf_life"):
        extra.append("<p><strong>保质期/储存：</strong>{}</p>".format(esc(product.get("shelf_life"))))
    return (
        '<div class="nutrition panel"><h4>营养成分表（{}）</h4>'
        '<div class="table-wrap"><table><thead><tr><th>项目</th><th>含量</th><th>NRV%</th></tr></thead>'
        "<tbody>{}</tbody></table></div>{}</div>"
    ).format(esc(basis), "".join(body), "".join(extra))


def evidence_html(product: Dict[str, Any]) -> str:
    rows = product.get("evidence")
    if not rows:
        return ""
    body = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        source = esc(item.get("source"))
        url = safe_url(item.get("url"))
        if url:
            source = '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>'.format(
                esc(url), source or "来源"
            )
        body.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(item.get("field")), esc(item.get("value")), source, esc(item.get("captured_at"))
            )
        )
    return (
        "<details><summary>查看数据来源（{} 条）</summary>"
        '<div class="table-wrap"><table><thead><tr><th>字段</th><th>值</th><th>来源</th><th>采集时间</th></tr></thead>'
        "<tbody>{}</tbody></table></div></details>"
    ).format(len(body), "".join(body))


def product_html(product: Dict[str, Any], generated: str) -> str:
    scores = product.get("scores") or {}
    rate_raw = scores.get("ai_rate")
    rate = int(round(as_float(rate_raw) or 0))
    tier_label, tier_class = tier_for(rate)
    name = esc(product.get("name")) or "未命名商品"
    url = safe_url(product.get("url"))
    name_html = (
        '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>'.format(esc(url), name)
        if url
        else name
    )
    shop_line = " · ".join(
        part
        for part in [
            esc(product.get("shop")),
            esc(product.get("shop_type")),
        ]
        if part
    )

    price_html = '<div class="price">{}</div>'.format(money(product.get("price")))
    original = as_float(product.get("original_price"))
    if original and as_float(product.get("price")) and original > (as_float(product.get("price")) or 0):
        price_html += '<div class="original">{}</div>'.format(money(original))
    if product.get("unit_price"):
        price_html += '<div class="unit">{}</div>'.format(esc(product.get("unit_price")))

    stats = [
        ("好评率", fmt_pct(product.get("positive_rate"))),
        ("销量", text(product.get("sales"))),
        ("评价数", fmt_num(product.get("review_count"))),
        ("商品评分", text(product.get("rating"))),
    ]
    stats_html = "".join(
        '<div class="stat"><b>{}</b><span>{}</span></div>'.format(esc(value), esc(label))
        for label, value in stats
    )

    specs_html = ""
    if isinstance(product.get("specs"), dict) and product["specs"]:
        specs_html = '<div class="specs">{}</div>'.format(
            "".join(
                '<span class="spec">{}：{}</span>'.format(esc(key), esc(value))
                for key, value in product["specs"].items()
            )
        )

    image_html = ""
    image_url = safe_url(product.get("image"))
    if image_url:
        image_html = (
            '<img src="{}" alt="{}" loading="lazy" referrerpolicy="no-referrer" '
            'style="width:100%;max-height:320px;object-fit:contain;background:#f8fafc;'
            'border-radius:12px;margin-bottom:16px" onerror="this.remove()">'
        ).format(esc(image_url), name)

    risk_html = ""
    if product.get("risk_tags"):
        risk_html = '<div class="risk">{}</div>'.format(
            "".join("<span>{}</span>".format(esc(tag)) for tag in product["risk_tags"])
        )

    details_html = (
        '<p class="details">{}</p>'.format(esc(product.get("details"))) if product.get("details") else ""
    )
    vs_html = (
        '<div class="panel"><h4>较同类产品的对比</h4><p>{}</p></div>'.format(esc(product.get("vs_peers")))
        if product.get("vs_peers")
        else ""
    )
    ai_html = (
        '<div class="panel ai"><h4>AI 推荐说明</h4><p>{}</p></div>'.format(esc(product.get("ai_reason")))
        if product.get("ai_reason")
        else ""
    )
    after_html = (
        '<p class="muted" style="font-size:13px"><strong>售后：</strong>{}</p>'.format(
            esc(product.get("after_sales"))
        )
        if product.get("after_sales")
        else ""
    )

    return """
<article class="product" id="p{rank}">
  <div class="product-head">
    <div class="rank-no">#{rank}</div>
    <div class="titles">
      <h3>{name_html}</h3>
      <p class="sub">{badge} <span>{shop_line}</span></p>
    </div>
    <div class="score-wrap">
      <div class="score-ring" style="--p:{rate};--ring:{ring}"><span>{rate}</span></div>
      <small>AI 推荐率</small>
      <span class="tier {tier_class}">{tier_label}</span>
    </div>
  </div>
  {image_html}
  <div class="price-row">{price_html}</div>
  <div class="stats">{stats_html}</div>
  {details_html}
  {specs_html}
  {nutrition_html}
  <div class="cols">
    <div><h4>优点</h4>{pros_html}</div>
    <div class="cons"><h4>缺点 / 注意事项</h4>{cons_html}</div>
  </div>
  {vs_html}
  {ai_html}
  {risk_html}
  {after_html}
  {evidence_html}
  <p class="muted" style="font-size:12.5px;margin-top:14px">来源：<a href="{url}" target="_blank" rel="noopener noreferrer">{platform} 商品页</a> ｜ 采集时间：{captured}</p>
</article>
""".format(
        rank=product.get("rank"),
        name_html=name_html,
        badge=badge(product.get("platform")),
        shop_line=shop_line or "店铺未获取",
        rate=rate,
        ring="#2563eb" if rate >= 80 else ("#d97706" if rate >= 70 else "#dc2626"),
        tier_class=tier_class,
        tier_label=tier_label,
        image_html=image_html,
        price_html=price_html,
        stats_html=stats_html,
        details_html=details_html,
        specs_html=specs_html,
        nutrition_html=nutrition_html(product),
        pros_html=html_list(product.get("pros"), "未总结"),
        cons_html=html_list(product.get("cons"), "未总结"),
        vs_html=vs_html,
        ai_html=ai_html,
        risk_html=risk_html,
        after_html=after_html,
        evidence_html=evidence_html(product),
        url=esc(url) if url else "#",
        platform=esc(product.get("platform")),
        captured=esc(last_captured(product, generated)),
    )


def render_html(data: Dict[str, Any]) -> str:
    meta = data.get("meta") or {}
    products = data.get("products") or []
    excluded = data.get("excluded") or []
    conclusion = data.get("conclusion") or {}
    generated = meta.get("generated_at") or ""
    title = meta.get("title") or "选购推荐报告"
    summary = meta.get("summary") or data.get("summary") or ""
    if not summary and products:
        summary = "综合首选：{}（{}）".format(text(products[0].get("name")), text(products[0].get("platform")))

    chips = [
        ("查询", text(meta.get("query"))),
        ("类型", text(meta.get("input_type"))),
        ("平台", text(meta.get("platforms"), "未指定")),
        ("预算", text(meta.get("budget"), "未指定")),
        ("生成", generated),
    ]
    chips_html = "".join("<span class=\"chip\">{}</span>".format(esc(value)) for _, value in chips)

    rank_cards = "".join(
        """
<a class="rank-card" href="#p{rank}">
  <span class="rank-no">{rank}</span>
  <div><strong>{name}</strong><small>{platform} · {price}</small></div>
  <span class="rank-score">{rate}</span>
</a>""".format(
            rank=product.get("rank"),
            name=esc(product.get("name")),
            platform=esc(product.get("platform")),
            price=money(product.get("price")),
            rate=esc((product.get("scores") or {}).get("ai_rate")),
        )
        for product in products
    )
    if not rank_cards:
        rank_cards = '<div class="empty">暂无通过筛选的商品。</div>'

    cards = "".join(product_html(product, generated) for product in products)
    if not cards:
        cards = '<div class="empty">本次没有通过筛选的商品。可放宽阈值或补充样本后重试。</div>'

    comparison_rows = []
    for product in products:
        scores = product.get("scores") or {}
        comparison_rows.append(
            "<tr><td><a href=\"#p{}\">{}</a></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td><strong>{}</strong></td></tr>".format(
                product.get("rank"),
                esc(product.get("name")),
                esc(product.get("platform")),
                money(product.get("price")),
                fmt_pct(product.get("positive_rate")),
                esc(product.get("sales")),
                esc(product.get("rating")),
                esc(product.get("shop")),
                esc(scores.get("ai_rate")),
            )
        )
    comparison = (
        '<div class="table-wrap"><table><thead><tr><th>商品</th><th>平台</th><th>到手价</th>'
        "<th>好评率</th><th>销量</th><th>评分</th><th>店铺</th><th>AI推荐率</th></tr></thead>"
        "<tbody>{}</tbody></table></div>".format("".join(comparison_rows))
        if comparison_rows
        else '<div class="empty">暂无横向对比数据。</div>'
    )

    if excluded:
        excluded_rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(item.get("name")), esc(item.get("platform")), esc(item.get("reason"))
            )
            for item in excluded
            if isinstance(item, dict)
        )
        excluded_html = (
            '<div class="table-wrap"><table><thead><tr><th>商品</th><th>平台</th><th>排除原因</th></tr></thead>'
            "<tbody>{}</tbody></table></div>".format(excluded_rows)
        )
    else:
        excluded_html = '<div class="empty">本次没有商品触发硬性排除规则。</div>'

    conclusion_html = ""
    conclusion_section = ""
    if conclusion:
        labels = [
            ("best_value", "性价比首选"),
            ("best_quality", "品质 / 售后首选"),
            ("budget_pick", "预算有限"),
            ("avoid", "建议避开"),
        ]
        items = [
            "<li><strong>{}</strong>：{}</li>".format(label, esc(conclusion[key]))
            for key, label in labels
            if conclusion.get(key)
        ]
        if items:
            conclusion_html = '<ul class="scenario-list">{}</ul>'.format("".join(items))
            conclusion_section = (
                "<section><h2>场景建议</h2>"
                '<div class="panel scenario">{}</div></section>'.format(conclusion_html)
            )

    methodology = text(
        data.get("methodology"),
        "评分维度：口碑 35%、销量 20%、价格 20%、店铺信任 15%、信息完整度 10%，再扣除风险分。",
    )

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header class="hero">
  <div class="wrap">
    <p class="eyebrow">E-commerce Shopping Report</p>
    <h1>{title}</h1>
    <div class="meta">{chips}</div>
    <p class="summary">{summary}</p>
    <div class="hero-stats">
      <div><b>{count}</b><span>推荐商品</span></div>
      <div><b>{platform_count}</b><span>覆盖平台</span></div>
      <div><b>{excluded_count}</b><span>已排除</span></div>
    </div>
  </div>
</header>
<main class="wrap">
  <section>
    <h2>推荐榜单</h2>
    <div class="rank-grid">{rank_cards}</div>
  </section>
  {conclusion_section}
  <section>
    <h2>推荐商品详情</h2>
    {cards}
  </section>
  <section>
    <h2>横向对比</h2>
    <p class="table-hint">表格可左右滑动查看完整列 →</p>
    {comparison}
  </section>
  <section>
    <h2>已排除商品</h2>
    {excluded_html}
  </section>
  <section>
    <h2>数据说明与免责声明</h2>
    <div class="panel"><p>{methodology}</p></div>
    <ul class="muted" style="font-size:13px">
      <li><strong>AI 推荐率</strong>是本报告模型的综合评分（0–100），不是平台官方指标，也不是购买概率。</li>
      <li>好评率/销量为平台页面展示口径；标注「推算」的字段由评分、评价数与差评情感推得。</li>
      <li>价格、优惠、库存会变动，下单前请以平台实时页面为准。</li>
      <li>本报告不含推广返利链接；商品图片与商标归原平台/商家所有。</li>
    </ul>
  </section>
</main>
<footer>
  <div class="wrap">
    <p>报告生成时间：{generated} ｜ 分析：{analyst}</p>
    <p>数据来源为各电商平台公开商品页，仅供选购参考。请遵守平台服务条款，勿用于批量抓取或商业转售。</p>
  </div>
</footer>
</body>
</html>
""".format(
        title=esc(title),
        css=CSS,
        chips=chips_html,
        summary=esc(summary) or "本次样本不足，未能形成明确结论。",
        count=len(products),
        platform_count=len({str(p.get("platform")) for p in products if p.get("platform")}),
        excluded_count=len(excluded),
        rank_cards=rank_cards,
        cards=cards,
        comparison=comparison,
        excluded_html=excluded_html,
        methodology=esc(methodology),
        conclusion_section=conclusion_section,
        generated=esc(generated),
        analyst=esc(meta.get("analyst") or "DSH"),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render e-commerce recommendation report (Markdown + HTML).")
    parser.add_argument("--input", required=True, help="path to products.json")
    parser.add_argument("--outdir", required=True, help="directory for report.md / report.html")
    parser.add_argument("--title", help="override report title")
    parser.add_argument("--quiet", action="store_true", help="only print output paths")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.input).expanduser()
    if not source.is_file():
        print("error: input not found: {}".format(source), file=sys.stderr)
        return 2

    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("error: cannot read JSON: {}".format(exc), file=sys.stderr)
        return 2

    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        print("error: products.json must contain a 'products' array", file=sys.stderr)
        return 2
    if not data["products"]:
        print("warning: 'products' is empty; rendering a no-result report", file=sys.stderr)

    if args.title:
        data.setdefault("meta", {})["title"] = args.title

    normalize(data)

    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    markdown_path = outdir / "report.md"
    html_path = outdir / "report.html"
    markdown_path.write_text(render_markdown(data), encoding="utf-8")
    html_path.write_text(render_html(data), encoding="utf-8")

    target_data = outdir / "products.json"
    if source.resolve() != target_data.resolve():
        shutil.copyfile(source, target_data)

    if not args.quiet:
        print("✓ {}".format(markdown_path))
        print("✓ {}".format(html_path))
        print("推荐 {} 个商品，排除 {} 个".format(len(data["products"]), len(data.get("excluded") or [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
