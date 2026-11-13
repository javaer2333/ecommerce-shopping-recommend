# 电商购物推荐 Skill

把一个商品链接、商品名称或商品类目，变成一份**可复核的跨平台选购报告**：真实搜索淘宝/天猫、京东、拼多多、闲鱼、抖音电商，抓取价格、销量、评分、好评率、详情与食品营养成分，过滤低好评/高风险商品，按统一模型评分，输出 Markdown 与单文件 HTML。

## 目录

```
ecommerce-shopping-recommend/
├── SKILL.md                       # 技能入口：触发条件、工作流、红线
├── README.md                      # 本文件
├── references/
│   ├── platforms.md               # 5 个平台的入口、字段、坑与采集模板
│   └── scoring.md                 # 数据 Schema、评分公式、过滤阈值、报告口径
├── scripts/
│   └── render_report.py           # products.json → report.md + report.html
├── examples/
│   └── sample-products.json       # 可直接改写的示例数据
└── tests/
    └── test_render.py             # 9 项离线自测
```

## 安装

DSH 从用户级技能目录加载技能。把整个文件夹放到：

```bash
~/.agents/skills/ecommerce-shopping-recommend
```

如果只是在工作区里使用，也可以保留在 `skills/ecommerce-shopping-recommend`，直接用路径调用脚本；要让技能出现在技能列表里，需要复制到 `~/.agents/skills/`。

安装后新开一轮对话（或刷新会话），技能列表中应出现 `ecommerce-shopping-recommend`。

## 怎么用

直接对 Agent 说：

- 「帮我挑一款 500 元以内的降噪耳机，要好评率高的」
- 「这个淘宝链接的同款，京东和拼多多哪个便宜？」+ 链接
- 「儿童保温杯怎么选，推荐 3 个」
- 「对比一下这三款猫粮，做成 HTML 报告」

Agent 会按 `SKILL.md` 的流程：解析输入 → 选平台 → 用 ego-browser 搜索并采集 → 过滤 → 评分 → 写 `products.json` → 调渲染脚本。

## 快速演示（不联网）

仓库自带一份示例数据，可直接看输出长什么样：

```bash
cd ~/.agents/skills/ecommerce-shopping-recommend
python3 scripts/render_report.py \
  --input examples/sample-products.json \
  --outdir /tmp/ecom-demo
open /tmp/ecom-demo/report.html
```

产物：

| 文件 | 用途 |
| --- | --- |
| `report.html` | 单文件、离线可开、响应式，适合直接发给别人 |
| `report.md` | 适合粘贴到飞书/Notion/微信 |
| `products.json` | 结构化数据与评分，便于复核或二次加工 |

## 输入数据

最小的 `products.json` 只需要：

```json
{
  "meta": {"query": "降噪耳机", "platforms": ["京东", "淘宝"]},
  "products": [
    {
      "name": "商品名",
      "platform": "京东",
      "url": "https://item.jd.com/xxx.html",
      "price": 499,
      "positive_rate": 98.2,
      "sales": "已售 2 万+",
      "review_count": 5000,
      "rating": 4.9
    }
  ],
  "excluded": []
}
```

其余字段（详情、规格、营养成分、优缺点、AI 推荐说明、证据）都是可选的；`scores` 不填时脚本会按公式自动补算。完整字段见 `references/scoring.md`。

## 评分口径

| 维度 | 权重 |
| --- | --- |
| 口碑质量（好评率/评分/评价量） | 35% |
| 销量热度 | 20% |
| 价格竞争力 | 20% |
| 店铺/品牌信任 | 15% |
| 信息完整度 | 10% |
| 风险扣分 | 0–15 |

`AI 推荐率 = 加权总分 − 风险扣分`，分档：≥90 强烈推荐、80–89 推荐、70–79 可考虑、60–69 谨慎、<60 不推荐。

**AI 推荐率是模型综合分，不是平台官方指标。** 好评率低于 95%（食品/母婴/美妆 96%）会先被硬过滤，不进入推荐位。

## 自测

```bash
cd ecommerce-shopping-recommend
python3 tests/test_render.py
```

覆盖：样例渲染、缺失评分补算、HTML 转义与非法 URL、空结果、非法 JSON、销量单位解析、价格/百分比格式、分档边界。

## 边界

- 只读取公开商品页，不使用他人账号、不导出个人信息、不保存 Cookie。
- 遇到登录/验证码/风控：请用户手动完成，不绕过。
- 不做批量爬取、不刷单、不伪造评价；不写「全网最低」等无法验证的结论。
- 平台页面结构与文案会变，采集时以可见文字标签为准；字段缺失写「未获取」，不编造。
- 价格、优惠、库存随时变化，下单前以平台实时页面为准。

## 维护

- 平台入口/字段变化：改 `references/platforms.md`
- 评分阈值/公式变化：同时改 `references/scoring.md` 和 `scripts/render_report.py`
- 报告样式：改 `scripts/render_report.py` 里的 `CSS` 常量
- 改完跑一遍 `python3 tests/test_render.py`
