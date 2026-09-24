from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class KeywordSpec:
    key: str
    concept_id: str
    query: str
    aliases: tuple[str, ...]
    language: str
    category: str
    kind: str = "base"
    priority: int = 50
    expires_at: datetime | None = None


# Durable concepts generate four bounded searches each: English/Chinese
# topic searches plus one category-specific event search in each language.
CONCEPTS: tuple[tuple[str, str, str, str], ...] = (
    ("gdp", "GDP", "国内生产总值", "macro"),
    ("inflation", "inflation", "通货膨胀", "macro"),
    ("cpi", "consumer price index", "居民消费价格指数", "macro"),
    ("ppi", "producer price index", "工业生产者出厂价格", "macro"),
    ("deflation", "deflation", "通货紧缩", "macro"),
    ("unemployment", "unemployment rate", "失业率", "macro"),
    ("jobs-report", "nonfarm payrolls", "非农就业报告", "macro"),
    ("interest-rates", "interest rates", "利率", "macro"),
    ("central-bank", "central bank", "中央银行", "macro"),
    ("federal-reserve", "Federal Reserve", "美联储", "macro"),
    ("pboc", "People's Bank of China", "中国人民银行", "macro"),
    ("ecb", "European Central Bank", "欧洲央行", "macro"),
    ("monetary-policy", "monetary policy", "货币政策", "macro"),
    ("fiscal-policy", "fiscal policy", "财政政策", "macro"),
    ("rate-cut", "interest rate cut", "降息", "macro"),
    ("economic-growth", "economic growth", "经济增长", "macro"),
    ("recession", "recession", "经济衰退", "macro"),
    ("economic-forecast", "economic forecast", "经济预测", "macro"),
    ("consumer-confidence", "consumer confidence", "消费者信心指数", "macro"),
    ("pmi", "purchasing managers index", "采购经理指数", "macro"),
    ("retail-sales", "retail sales", "社会消费品零售总额", "macro"),
    ("industrial-production", "industrial production", "工业增加值", "macro"),
    ("fixed-asset-investment", "fixed asset investment", "固定资产投资", "macro"),
    ("government-debt", "government debt", "政府债务", "macro"),
    ("budget-deficit", "budget deficit", "财政赤字", "macro"),
    ("stimulus", "economic stimulus", "经济刺激", "macro"),
    ("liquidity", "market liquidity", "市场流动性", "macro"),
    ("m2-money-supply", "M2 money supply", "M2 货币供应量", "macro"),
    ("credit-growth", "credit growth", "信贷增长", "macro"),
    ("economic-survey", "economic survey", "经济调查", "macro"),
    ("stock-market", "stock market", "股票市场", "markets"),
    ("a-shares", "China A-shares", "A股", "markets"),
    ("hong-kong-stocks", "Hong Kong stocks", "港股", "markets"),
    ("us-stocks", "US stocks", "美股", "markets"),
    ("nasdaq", "Nasdaq", "纳斯达克", "markets"),
    ("sp500", "S&P 500", "标普500", "markets"),
    ("bond-market", "bond market", "债券市场", "markets"),
    ("treasury-yields", "treasury yields", "国债收益率", "markets"),
    ("exchange-rate", "exchange rate", "汇率", "markets"),
    ("yuan-exchange-rate", "yuan exchange rate", "人民币汇率", "markets"),
    ("dollar-index", "US dollar index", "美元指数", "markets"),
    ("gold-price", "gold price", "黄金价格", "markets"),
    ("oil-price", "crude oil price", "原油价格", "markets"),
    ("commodity-prices", "commodity prices", "大宗商品价格", "markets"),
    ("copper-price", "copper price", "铜价", "markets"),
    ("forex-market", "foreign exchange market", "外汇市场", "markets"),
    ("etf", "ETF funds", "ETF 基金", "markets"),
    ("ipo", "IPO market", "IPO 市场", "markets"),
    ("hedge-fund", "hedge funds", "对冲基金", "markets"),
    ("quantitative-trading", "quantitative trading", "量化交易", "markets"),
    ("futures-market", "futures market", "期货市场", "markets"),
    ("options-market", "options market", "期权市场", "markets"),
    ("reits", "REITs", "不动产投资信托基金", "markets"),
    ("bitcoin", "bitcoin", "比特币", "markets"),
    ("digital-currency", "digital currency", "数字货币", "markets"),
    ("trade-policy", "trade policy", "贸易政策", "policy"),
    ("tariffs", "tariffs", "关税", "policy"),
    ("trade-war", "trade war", "贸易战", "policy"),
    ("sanctions", "economic sanctions", "经济制裁", "policy"),
    ("export-controls", "export controls", "出口管制", "policy"),
    ("antitrust", "antitrust regulation", "反垄断监管", "policy"),
    ("financial-regulation", "financial regulation", "金融监管", "policy"),
    ("banking-regulation", "banking regulation", "银行监管", "policy"),
    ("tax-policy", "tax policy", "税收政策", "policy"),
    ("industrial-policy", "industrial policy", "产业政策", "policy"),
    ("subsidy-policy", "government subsidies", "政府补贴", "policy"),
    ("capital-controls", "capital controls", "资本管制", "policy"),
    ("debt-ceiling", "debt ceiling", "债务上限", "policy"),
    ("free-trade-agreement", "free trade agreement", "自由贸易协定", "policy"),
    ("cbdc", "central bank digital currency", "央行数字货币", "policy"),
    ("real-estate", "real estate market", "房地产市场", "industry"),
    ("manufacturing", "manufacturing sector", "制造业", "industry"),
    ("supply-chain", "supply chain", "供应链", "industry"),
    ("semiconductor-industry", "semiconductor industry", "半导体产业", "industry"),
    ("automotive-industry", "automotive industry", "汽车产业", "industry"),
    ("electric-vehicle-market", "electric vehicle market", "电动汽车市场", "industry"),
    ("energy-market", "energy market", "能源市场", "industry"),
    ("renewable-energy", "renewable energy", "可再生能源", "industry"),
    ("steel-industry", "steel industry", "钢铁行业", "industry"),
    ("shipping-industry", "shipping industry", "航运业", "industry"),
    ("logistics", "logistics industry", "物流行业", "industry"),
    ("agriculture-economy", "agricultural economy", "农业经济", "industry"),
    ("tourism-industry", "tourism industry", "旅游业", "industry"),
    ("consumer-market", "consumer market", "消费市场", "industry"),
    ("labor-market", "labor market", "劳动力市场", "industry"),
    ("corporate-earnings", "corporate earnings", "企业财报", "business"),
    ("mergers-acquisitions", "mergers and acquisitions", "企业并购", "business"),
    ("bankruptcy", "corporate bankruptcy", "企业破产", "business"),
    ("layoffs", "corporate layoffs", "企业裁员", "business"),
    ("venture-capital", "venture capital", "风险投资", "business"),
    ("private-equity", "private equity", "私募股权", "business"),
    ("banking-sector", "banking sector", "银行业", "business"),
    ("insurance-industry", "insurance industry", "保险业", "business"),
    ("fintech", "fintech industry", "金融科技", "business"),
    ("global-economy", "global economy", "全球经济", "business"),
    ("china-economy", "Chinese economy", "中国经济", "business"),
    ("us-economy", "US economy", "美国经济", "business"),
    ("emerging-markets", "emerging markets", "新兴市场", "business"),
    ("european-economy", "European economy", "欧洲经济", "business"),
    ("japan-economy", "Japanese economy", "日本经济", "business"),
)

EVENT_MODIFIERS = {
    "macro": ("data outlook", "数据 展望"),
    "markets": ("rally outlook", "行情 展望"),
    "policy": ("decision regulation impact", "决议 监管 影响"),
    "industry": ("output investment outlook", "产量 投资 展望"),
    "business": ("earnings investment deal", "财报 投资 交易"),
}


def _key(concept_id: str, language: str, query_type: str) -> str:
    return f"base:{concept_id}:{language}:{query_type}"


def base_keyword_specs() -> tuple[KeywordSpec, ...]:
    specs: list[KeywordSpec] = []
    for concept_id, english, chinese, category in CONCEPTS:
        en_event, zh_event = EVENT_MODIFIERS[category]
        for language, phrase, peer, event in (
            ("en", english, chinese, en_event),
            ("zh", chinese, english, zh_event),
        ):
            aliases = tuple(dict.fromkeys((phrase, peer)))
            specs.append(KeywordSpec(
                _key(concept_id, language, "topic"), concept_id, phrase,
                aliases, language, category, priority=70,
            ))
            specs.append(KeywordSpec(
                _key(concept_id, language, "event"), concept_id,
                f'"{phrase}" {event}', aliases, language, category, priority=55,
            ))
    return tuple(specs)


QUERY_VARIANTS: tuple[tuple[str, str], ...] = (
    ("analysis", "latest report"),
    ("analysis", "weekly outlook"),
    ("analysis", "monthly data"),
    ("analysis", "market analysis"),
    ("analysis", "policy impact"),
    ("analysis", "investment research"),
    ("analysis", "risk assessment"),
    ("analysis", "industry analysis"),
    ("analysis", "earnings update"),
    ("analysis", "forecast 2026"),
    ("analysis", "data release"),
    ("analysis", "expert commentary"),
    ("source", "Reuters report"),
    ("source", "Bloomberg analysis"),
    ("source", "Financial Times article"),
    ("source", "WSJ story"),
    ("source", "Nikkei Asia article"),
    ("source", "SCMP report"),
    ("source", "Caixin article"),
    ("source", "Yicai report"),
    ("source", "Reuters exclusive"),
    ("source", "Bloomberg research"),
    ("source", "FT commentary"),
    ("source", "WSJ analysis"),
    ("region", "United States"),
    ("region", "China"),
    ("region", "Eurozone"),
    ("region", "Japan"),
    ("region", "India"),
    ("region", "emerging markets"),
    ("region", "Asia Pacific"),
    ("region", "Europe"),
    ("region", "global economy"),
    ("region", "US China"),
    ("region", "G20"),
    ("region", "ASEAN"),
    ("timing", "breaking news"),
    ("timing", "latest update"),
    ("timing", "weekly review"),
    ("timing", "monthly outlook"),
    ("timing", "quarterly review"),
    ("timing", "annual outlook"),
    ("timing", "five year forecast"),
    ("timing", "historical data"),
    ("timing", "statistics database"),
    ("timing", "press release"),
    ("timing", "official statement"),
    ("timing", "research summary"),
)

QUERY_VARIANTS_ZH: tuple[tuple[str, str], ...] = (
    ("analysis", "最新 报告"),
    ("analysis", "每周 展望"),
    ("analysis", "月度 数据"),
    ("analysis", "市场 分析"),
    ("analysis", "政策 影响"),
    ("analysis", "投资 研究"),
    ("analysis", "风险 评估"),
    ("analysis", "行业 分析"),
    ("analysis", "财报 更新"),
    ("analysis", "2026 预测"),
    ("analysis", "数据 发布"),
    ("analysis", "专家 解读"),
    ("source", "路透 报道"),
    ("source", "彭博 分析"),
    ("source", "金融时报 文章"),
    ("source", "华尔街日报 报道"),
    ("source", "日经亚洲 文章"),
    ("source", "南华早报 报道"),
    ("source", "财新 文章"),
    ("source", "第一财经 报道"),
    ("source", "路透 独家"),
    ("source", "彭博 研究"),
    ("source", "金融时报 评论"),
    ("source", "华尔街日报 分析"),
    ("region", "美国"),
    ("region", "中国"),
    ("region", "欧元区"),
    ("region", "日本"),
    ("region", "印度"),
    ("region", "新兴市场"),
    ("region", "亚太"),
    ("region", "欧洲"),
    ("region", "全球经济"),
    ("region", "中美"),
    ("region", "G20"),
    ("region", "东盟"),
    ("timing", "最新 消息"),
    ("timing", "最新 更新"),
    ("timing", "每周 回顾"),
    ("timing", "月度 展望"),
    ("timing", "季度 回顾"),
    ("timing", "年度 展望"),
    ("timing", "五年 预测"),
    ("timing", "历史 数据"),
    ("timing", "统计 数据库"),
    ("timing", "新闻 发布会"),
    ("timing", "官方 声明"),
    ("timing", "研究 摘要"),
)


def expanded_keyword_specs() -> tuple[KeywordSpec, ...]:
    """Return base concepts plus bounded, deterministic query variants.

    Variants keep the original concept aliases for relevance filtering. The
    stable variant key makes restarts idempotent and prevents duplicate work.
    """
    specs = list(base_keyword_specs())
    for concept_id, english, chinese, category in CONCEPTS:
        for language, phrase in (("en", english), ("zh", chinese)):
            aliases = tuple(dict.fromkeys((phrase, chinese if language == "en" else english)))
            variants = QUERY_VARIANTS if language == "en" else QUERY_VARIANTS_ZH
            for variant_type, modifier in variants:
                specs.append(KeywordSpec(
                    f"{_key(concept_id, language, 'variant')}:{variant_type}:{modifier}",
                    concept_id, f'"{phrase}" {modifier}', aliases, language, category,
                    priority=60,
                ))
    return tuple(specs)


def catalog_keyword_specs() -> tuple[KeywordSpec, ...]:
    if os.getenv("CONTINUOUS_QUERY_VARIANTS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        return expanded_keyword_specs()
    return base_keyword_specs()


ECONOMY_ANCHORS = re.compile(
    r"\b(?:economy|economic|inflation|gdp|central bank|interest rates?|"
    r"stock market|bonds?|tariffs?|recession|monetary|fiscal)\b|"
    r"经济|通货膨胀|通胀|央行|利率|股市|债券|关税|财政|货币|金融|汇率|国内生产总值",
    re.IGNORECASE,
)

# These are precise economy concepts omitted by the generic guard. They are
# title-only so navigation links cannot make unrelated bodies eligible.
ECONOMY_SPECIFIC_ANCHORS = re.compile(
    r"\b(?:"
    r"federal reserve|consumer price index|producer price index|"
    r"treasury yields|exchange rates?|supply chains?|venture capital|"
    r"private equity|mergers and acquisitions|quantitative easing|"
    r"purchasing managers index|nonfarm payrolls|"
    r"fixed asset investment|central bank digital currency)\b|"
    r"美联储|欧洲央行|通货紧缩|居民消费价格指数|采购经理指数|"
    r"非农就业|供应链|风险投资|私募股权|企业并购|降息|降准|"
    r"量化宽松|固定资产投资|央行数字货币",
    re.IGNORECASE,
)

ECONOMY_TITLE_ACRONYMS = re.compile(
    r"(?<![a-z0-9])(?:gdp|cpi|ppi|pmi|etf|ipo|m2|fed)(?![a-z0-9])",
    re.IGNORECASE,
)


def has_economy_context(title: str, text: str) -> bool:
    combined = title + ' ' + text
    if ECONOMY_ANCHORS.search(combined):
        return True
    return bool(
        ECONOMY_TITLE_ACRONYMS.search(title)
        or ECONOMY_SPECIFIC_ANCHORS.search(title)
    )
