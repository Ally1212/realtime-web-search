from __future__ import annotations

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
    ("foundation-model", "AI foundation model", "人工智能基础模型", "models"),
    ("mixture-of-experts", "mixture of experts AI", "混合专家模型", "models"),
    ("diffusion-model", "AI diffusion model", "人工智能扩散模型", "models"),
    ("world-model", "AI world model", "人工智能世界模型", "models"),
    ("embodied-ai", "embodied AI", "具身智能", "models"),
    ("neural-network", "neural network AI", "人工智能神经网络", "research"),
    ("chain-of-thought", "AI chain of thought reasoning", "人工智能思维链", "research"),
    ("model-compression", "AI model compression", "人工智能模型压缩", "research"),
    ("training-data", "AI training data", "人工智能训练数据", "research"),
    ("benchmark", "AI benchmark", "人工智能基准测试", "research"),
    ("agentic-ai", "agentic AI", "智能体人工智能", "developer"),
    ("mcp", "AI model context protocol", "人工智能模型上下文协议", "developer"),
    ("workflow", "AI workflow automation", "人工智能工作流自动化", "developer"),
    ("no-code", "no code AI", "无代码人工智能", "developer"),
    ("ai-browser", "AI browser agent", "人工智能浏览器智能体", "developer"),
    ("npu", "AI NPU", "人工智能 NPU", "infrastructure"),
    ("tpu", "AI TPU", "人工智能 TPU", "infrastructure"),
    ("edge-ai", "edge AI computing", "边缘人工智能", "infrastructure"),
    ("ai-server", "AI server", "人工智能服务器", "infrastructure"),
    ("hbm", "AI high bandwidth memory", "人工智能高带宽内存", "infrastructure"),
    ("ethics", "AI ethics", "人工智能伦理", "governance"),
    ("governance", "AI governance", "人工智能治理", "governance"),
    ("responsible-ai", "responsible AI", "负责任人工智能", "governance"),
    ("risk", "AI risk management", "人工智能风险管理", "governance"),
    ("content-provenance", "AI content provenance", "人工智能内容溯源", "governance"),
    ("investment", "AI investment", "人工智能投资", "business"),
    ("market", "AI market", "人工智能市场", "business"),
    ("commercialization", "AI commercialization", "人工智能商业化", "business"),
    ("company", "AI company", "人工智能企业", "business"),
    ("agriculture", "agriculture AI", "农业人工智能", "applications"),
    ("energy", "energy AI", "能源人工智能", "applications"),
    ("pharma", "pharmaceutical AI", "医药人工智能", "applications"),
    ("ecommerce", "ecommerce AI", "电商人工智能", "applications"),
    ("customer-service", "customer service AI", "客服人工智能", "applications"),
    ("office", "office automation AI", "办公自动化人工智能", "applications"),
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

# These are precise AI concepts omitted by the original generic guard. They
# are title-only so navigation links cannot make unrelated bodies eligible.
AI_SPECIFIC_ANCHORS = re.compile(
    r"\b(?:"
    r"large language models?|small language models?|neural networks?|"
    r"reinforcement learning|foundation models?|diffusion models?|"
    r"retrieval[- ]augmented generation|natural language processing|"
    r"computer vision|vision language models?|generative models?|"
    r"prompt engineering|deepfakes?|model context protocol|"
    r"openai|chatgpt|deepseek|qwen)\b|"
    r"大语言模型|深度学习|神经网络|"
    r"强化学习|扩散模型|检索增强生成|自然语言处理|计算机视觉|视觉语言模型|"
    r"提示词工程|深度伪造|具身智能|思维链|通义千问",
    re.IGNORECASE,
)

AI_TITLE_ACRONYMS = re.compile(
    r"(?<![a-z0-9])(?:ai|llms?|gpt(?:-?\d+)?)(?![a-z0-9])",
    re.IGNORECASE,
)


def has_ai_context(title: str, text: str) -> bool:
    combined = title + ' ' + text
    if AI_ANCHORS.search(combined):
        return True
    return bool(AI_TITLE_ACRONYMS.search(title) or AI_SPECIFIC_ANCHORS.search(title))
