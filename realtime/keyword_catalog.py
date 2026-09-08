from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

try:
    import jieba
except ImportError:  # Source checkouts can still use the conservative fallback.
    jieba = None


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
    ("artificial-intelligence", "artificial intelligence", "人工智能", "foundations"),
    ("generative-ai", "generative AI", "生成式人工智能", "foundations"),
    ("machine-learning", "machine learning", "机器学习", "foundations"),
    ("deep-learning", "deep learning", "深度学习", "foundations"),
    ("agi", "artificial general intelligence", "通用人工智能", "foundations"),
    ("llm", "large language model", "大语言模型", "models"),
    ("multimodal", "multimodal AI", "多模态人工智能", "models"),
    ("reasoning-model", "AI reasoning model", "人工智能推理模型", "models"),
    ("small-language-model", "small language model", "小语言模型", "models"),
    ("vision-language-model", "vision language model", "视觉语言模型", "models"),
    ("image-generation", "AI image generation", "人工智能图像生成", "models"),
    ("video-generation", "AI video generation", "人工智能视频生成", "models"),
    ("speech-ai", "speech AI", "语音人工智能", "models"),
    ("rag", "retrieval augmented generation", "检索增强生成", "research"),
    ("fine-tuning", "AI model fine tuning", "人工智能模型微调", "research"),
    ("distillation", "AI model distillation", "人工智能模型蒸馏", "research"),
    ("quantization", "AI model quantization", "人工智能模型量化", "research"),
    ("synthetic-data", "AI synthetic data", "人工智能合成数据", "research"),
    ("reinforcement-learning", "AI reinforcement learning", "人工智能强化学习", "research"),
    ("model-evaluation", "AI model evaluation benchmark", "人工智能模型评测", "research"),
    ("inference-optimization", "AI inference optimization", "人工智能推理优化", "research"),
    ("agents", "AI agents", "人工智能智能体", "developer"),
    ("copilots", "AI copilots", "人工智能助手", "developer"),
    ("coding", "AI coding", "人工智能编程", "developer"),
    ("search", "AI search", "人工智能搜索", "developer"),
    ("api", "AI model API", "人工智能模型接口", "developer"),
    ("prompt-engineering", "prompt engineering", "提示词工程", "developer"),
    ("open-source", "open source AI", "开源人工智能", "developer"),
    ("chips", "AI chips", "人工智能芯片", "infrastructure"),
    ("gpu", "AI GPU", "人工智能 GPU", "infrastructure"),
    ("accelerator", "AI accelerator", "人工智能加速器", "infrastructure"),
    ("data-center", "AI data center", "人工智能数据中心", "infrastructure"),
    ("cloud", "AI cloud computing", "人工智能云计算", "infrastructure"),
    ("vector-database", "vector database AI", "人工智能向量数据库", "infrastructure"),
    ("safety", "AI safety", "人工智能安全", "governance"),
    ("alignment", "AI alignment", "人工智能对齐", "governance"),
    ("model-security", "AI model security", "人工智能模型安全", "governance"),
    ("deepfake", "AI deepfake", "人工智能深度伪造", "governance"),
    ("privacy", "AI privacy", "人工智能隐私", "governance"),
    ("copyright", "AI copyright", "人工智能版权", "governance"),
    ("regulation", "AI regulation", "人工智能监管", "governance"),
    ("startup", "AI startup", "人工智能创业公司", "business"),
    ("funding", "AI funding", "人工智能融资", "business"),
    ("merger", "AI acquisition merger", "人工智能并购", "business"),
    ("enterprise", "enterprise AI", "企业人工智能", "business"),
    ("product-launch", "AI product launch", "人工智能产品发布", "business"),
    ("healthcare", "healthcare AI", "医疗人工智能", "applications"),
    ("finance", "finance AI", "金融人工智能", "applications"),
    ("education", "education AI", "教育人工智能", "applications"),
    ("robotics", "AI robotics", "人工智能机器人", "applications"),
    ("legal", "legal AI", "法律人工智能", "applications"),
    ("manufacturing", "manufacturing AI", "制造业人工智能", "applications"),
    ("retail", "retail AI", "零售人工智能", "applications"),
    ("media", "media AI", "媒体人工智能", "applications"),
    ("gaming", "gaming AI", "游戏人工智能", "applications"),
    ("cybersecurity", "cybersecurity AI", "网络安全人工智能", "applications"),
    ("science", "AI scientific discovery", "人工智能科学发现", "applications"),
    ("climate", "climate AI", "气候人工智能", "applications"),
    ("autonomous-driving", "autonomous driving AI", "自动驾驶人工智能", "applications"),
    ("openai-chatgpt", "OpenAI ChatGPT", "OpenAI ChatGPT", "models"),
    ("anthropic-claude", "Anthropic Claude", "Anthropic Claude", "models"),
    ("google-gemini", "Google Gemini", "谷歌 Gemini", "models"),
    ("meta-llama", "Meta Llama", "Meta Llama", "models"),
    ("deepseek", "DeepSeek AI", "DeepSeek 人工智能", "models"),
    ("qwen", "Alibaba Qwen AI", "阿里通义千问", "models"),
)

EVENT_MODIFIERS = {
    "foundations": ("news research", "新闻 研究"),
    "models": ("launch update benchmark", "发布 更新 评测"),
    "research": ("research paper benchmark", "研究 论文 评测"),
    "developer": ("release open source update", "发布 开源 更新"),
    "infrastructure": ("launch investment capacity", "发布 投资 算力"),
    "governance": ("policy law incident", "政策 法规 事件"),
    "business": ("funding acquisition partnership", "融资 收购 合作"),
    "applications": ("deployment product research", "应用 产品 研究"),
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


AI_ANCHORS = re.compile(
    r"\b(?:ai|artificial intelligence|llm|gpt|machine learning|deep learning)\b|"
    r"人工智能|大模型|生成式|机器学习|智能体|机器人",
    re.IGNORECASE,
)
EN_CANDIDATE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9.-]*(?:\s+[A-Z][A-Za-z0-9.-]*){0,3}|"
    r"(?:GPT|Llama|Claude|Gemini|Qwen|DeepSeek|Mistral)[-\s]?[A-Za-z0-9.]+)\b"
)
ZH_CANDIDATE = re.compile(r"[\u3400-\u9fff]{2,10}")
TREND_BLACKLIST = {
    "artificial intelligence", "machine learning", "generative ai", "ai", "news",
    "latest news", "人工智能", "机器学习", "生成式人工智能", "最新消息", "科技新闻",
    "english", "chinese", "today", "latest", "new", "report", "research", "update",
    "the", "how", "why", "what", "us", "uk", "china", "world", "technology",
    "article", "article display", "home", "page", "image", "video", "read more",
    "american", "british", "european", "german", "french", "spanish", "italian",
    "japanese", "korean", "indian", "canadian", "australian", "singaporean",
    "europe", "asia", "africa", "america", "germany", "france", "japan", "india",
    "中文", "英文", "今日", "最新", "新闻", "报告", "研究", "更新", "中国", "全球",
    "文章", "首页", "页面", "图片", "视频", "阅读全文",
    "a", "an", "and", "are", "as", "at", "be", "billion", "but", "by",
    "can", "could", "education", "everything", "for", "from", "future", "in",
    "is", "it", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december", "more",
    "no", "of", "on", "one", "or", "should", "that", "this", "to", "top",
    "was", "we", "were", "when", "where", "which", "who", "will", "with", "you",
    "big tech", "cloud", "business", "nasdaq", "london", "brazil", "israel",
    "hyderabad", "assam", "u.s", "u.s.",
}


def _normalized_candidate(value: str) -> str:
    return " ".join(value.strip(" -–—:：,，.。'\"()（）").split())


def trend_candidate_allowed(value: str, excluded: set[str] | None = None) -> bool:
    normalized = _normalized_candidate(value)
    folded = normalized.casefold()
    without_anchor = _normalized_candidate(AI_ANCHORS.sub("", normalized)).casefold()
    excluded_values = {item.casefold() for item in (excluded or set())}
    if (
        len(normalized) < 2 or folded in TREND_BLACKLIST or folded in excluded_values
        or without_anchor in TREND_BLACKLIST or without_anchor in excluded_values
        or AI_ANCHORS.fullmatch(normalized)
    ):
        return False
    if re.fullmatch(r"[A-Za-z0-9. -]+", normalized):
        tokens = normalized.split()
        if not 1 <= len(tokens) <= 4 or all(token.casefold() in TREND_BLACKLIST for token in tokens):
            return False
        if len(tokens) == 1 and len(tokens[0].strip(".")) < 3:
            return False
    return True


def trend_keyword_specs(
    rows: list[dict[str, str]], *, now: datetime | None = None, limit: int = 50,
    excluded: set[str] | None = None,
) -> tuple[KeywordSpec, ...]:
    """Extract auditable trends backed by >=3 domains and >=5 distinct titles."""
    domains: dict[tuple[str, str], set[str]] = defaultdict(set)
    titles: dict[tuple[str, str], set[str]] = defaultdict(set)
    display: dict[tuple[str, str], str] = {}
    excluded_values = {value.casefold() for value in (excluded or set())}
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "")
        if not AI_ANCHORS.search(title):
            continue
        domain = (urlsplit(str(row.get("url") or "")).hostname or "").lower()
        candidates: list[tuple[str, str]] = []
        candidates.extend(("en", value) for value in EN_CANDIDATE.findall(title))
        chinese_chunks = ZH_CANDIDATE.findall(title)
        if jieba:
            candidates.extend(
                ("zh", token) for chunk in chinese_chunks
                for token in jieba.cut(chunk) if 2 <= len(token) <= 10
            )
        else:
            candidates.extend(("zh", value) for value in chinese_chunks)
        for language, raw in candidates:
            value = _normalized_candidate(raw)
            folded = value.casefold()
            if not trend_candidate_allowed(value, excluded_values):
                continue
            key = (language, folded)
            display.setdefault(key, value)
            if domain:
                domains[key].add(domain)
            titles[key].add(title)
    ranked = sorted(
        (key for key in titles if len(domains[key]) >= 3 and len(titles[key]) >= 5),
        key=lambda key: (-len(domains[key]), -len(titles[key]), key[1]),
    )[:max(0, limit)]
    current = now or datetime.now(timezone.utc)
    specs: list[KeywordSpec] = []
    for language, folded in ranked:
        phrase = display[(language, folded)]
        digest = hashlib.sha256(f"{language}:{folded}".encode()).hexdigest()[:16]
        query = f'"{phrase}" AI' if language == "en" else f'"{phrase}" 人工智能'
        specs.append(KeywordSpec(
            f"trend:{digest}", f"trend-{digest}", query, (phrase,), language,
            "trends", kind="trend", priority=60,
            expires_at=current + timedelta(days=7),
        ))
    return tuple(specs)
