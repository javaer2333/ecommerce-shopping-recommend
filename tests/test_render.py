#!/usr/bin/env python3
"""Offline tests for the e-commerce report renderer."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_report.py"
SAMPLE = ROOT / "examples" / "sample-products.json"


def load_module():
    spec = importlib.util.spec_from_file_location("render_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_report(source: Path, outdir: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--outdir", str(outdir)],
        capture_output=True,
        text=True,
    )


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmp.name) / "out"
        self.outdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sample_renders_both_formats(self):
        result = run_report(SAMPLE, self.outdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        markdown = (self.outdir / "report.md").read_text(encoding="utf-8")
        html = (self.outdir / "report.html").read_text(encoding="utf-8")

        self.assertIn("# 每日坚果 750g 装选购推荐报告", markdown)
        self.assertIn("## 推荐商品详情", markdown)
        self.assertIn("## 横向对比", markdown)
        self.assertIn("## 已排除商品", markdown)
        self.assertIn("营养成分表", markdown)
        self.assertIn("京东京造", markdown)
        self.assertIn("某杂牌每日坚果", markdown)

        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("推荐榜单", html)
        self.assertIn("AI 推荐率", html)
        self.assertIn("京东京造", html)
        self.assertIn("营养成分表", html)
        self.assertEqual(html.count("<article class=\"product\""), 3)

    def test_scores_are_computed_when_missing(self):
        module = load_module()
        data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        for product in data["products"]:
            product.pop("scores", None)
        module.normalize(data)
        for product in data["products"]:
            scores = product["scores"]
            for key in ("quality", "sales", "price", "trust", "info", "total", "ai_rate"):
                self.assertIsInstance(scores.get(key), (int, float), key)
            self.assertGreaterEqual(scores["ai_rate"], 0)
            self.assertLessEqual(scores["ai_rate"], 100)
        self.assertEqual([p["rank"] for p in data["products"]], [1, 2, 3])
        self.assertGreaterEqual(data["products"][0]["scores"]["ai_rate"], data["products"][1]["scores"]["ai_rate"])

    def test_html_escapes_user_content_and_rejects_bad_urls(self):
        data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        data["products"] = data["products"][:1]
        data["products"][0]["name"] = "<script>alert(1)</script> 坚果"
        data["products"][0]["details"] = "<img src=x onerror=alert(2)>"
        data["products"][0]["url"] = "javascript:alert(3)"
        source = Path(self.tmp.name) / "xss.json"
        source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = run_report(source, self.outdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = (self.outdir / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(2)>", html)
        self.assertNotIn("javascript:alert(3)", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", html)

    def test_empty_products_renders_no_result_report(self):
        source = Path(self.tmp.name) / "empty.json"
        source.write_text(json.dumps({"meta": {}, "products": []}), encoding="utf-8")
        result = run_report(source, self.outdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rendering a no-result report", result.stderr)
        html = (self.outdir / "report.html").read_text(encoding="utf-8")
        self.assertIn("暂无通过筛选的商品", html)
        markdown = (self.outdir / "report.md").read_text(encoding="utf-8")
        self.assertIn("本次样本不足", markdown)

    def test_rejects_non_array_products(self):
        source = Path(self.tmp.name) / "bad-shape.json"
        source.write_text(json.dumps({"meta": {}, "products": {}}), encoding="utf-8")
        result = run_report(source, self.outdir)
        self.assertEqual(result.returncode, 2)
        self.assertIn("'products' array", result.stderr)

    def test_rejects_invalid_json(self):
        source = Path(self.tmp.name) / "broken.json"
        source.write_text("{not json", encoding="utf-8")
        result = run_report(source, self.outdir)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read JSON", result.stderr)


class ParsingTests(unittest.TestCase):
    def test_sales_units(self):
        module = load_module()
        cases = {
            "已售 2.3万+": 23000,
            "1.2亿": 120000000,
            "已拼 8 千件": 8000,
            "5000+": 5000,
            "1.5w": 15000,
            2300: 2300,
        }
        for raw, expected in cases.items():
            self.assertEqual(module.parse_sales_num(raw), expected, raw)

    def test_price_and_percent_formatting(self):
        module = load_module()
        self.assertEqual(module.money(89), "¥89")
        self.assertEqual(module.money(89.5), "¥89.50")
        self.assertEqual(module.money(None), "未获取")
        self.assertEqual(module.fmt_pct(98.6), "98.6%")
        self.assertEqual(module.fmt_pct(98.0), "98%")

    def test_tiers(self):
        module = load_module()
        self.assertEqual(module.tier_for(95)[0], "强烈推荐")
        self.assertEqual(module.tier_for(85)[0], "推荐")
        self.assertEqual(module.tier_for(75)[0], "可考虑")
        self.assertEqual(module.tier_for(65)[0], "谨慎")
        self.assertEqual(module.tier_for(59)[0], "不推荐")


if __name__ == "__main__":
    unittest.main(verbosity=2)
