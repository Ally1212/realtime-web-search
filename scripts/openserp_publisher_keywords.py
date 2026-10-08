"""Candidate keyword variant; deploy only after the current comparison window."""

PUBLISHERS = [
    ("", ""),
    ("sohu.com", "allrecipes.com"),
    ("163.com", "foodnetwork.com"),
    ("qq.com", "food.com"),
    ("familydoctor.cn", "loveandlemons.com"),
    ("baike.baidu.com", "binkysculinarycarnival.com"),
    ("meishichina.com", "cdkitchen.com"),
    ("xinshipu.com", "savoryonline.com"),
    ("douguo.com", "familyfreshmeals.com"),
    ("xiachufang.com", "dinnersdonequick.com"),
    ("meishijie.com", "minuterice.com"),
    ("39.net", "hamiltonbeach.com"),
]
FACETS = [
    ("食谱", "recipes"), ("烹饪", "cooking"), ("营养", "nutrition"),
    ("保存", "storage"), ("食品安全", "food safety"), ("备餐", "meal prep"),
    ("冷冻", "freezing"), ("解冻", "thawing"), ("调味", "seasoning"),
    ("搭配", "pairings"), ("替代", "substitutes"), ("分量", "portions"),
    ("购买", "buying"), ("挑选", "selection"), ("处理", "preparation"),
    ("烤制", "roasting"), ("煎制", "frying"), ("蒸制", "steaming"),
    ("烧烤", "grilling"), ("汤", "soup"), ("沙拉", "salad"),
    ("早餐", "breakfast"), ("晚餐", "dinner"), ("快手", "quick recipes"),
    ("剩菜", "leftovers"), ("空气炸锅", "air fryer"), ("一锅", "one pot"),
    ("慢炖", "slow cooker"), ("压力锅", "pressure cooker"), ("常见错误", "cooking mistakes"),
]


def configure(module):
    module.SETTINGS = [tuple("site:" + host if host else "" for host in pair) for pair in PUBLISHERS]
    module.FACETS = FACETS
    module.BASE_CATALOG_SIZE = len(module.PLANTS) * len(module.FACETS) * len(module.SETTINGS)
    module.CATALOG_SIZE = module.BASE_CATALOG_SIZE * len(module.CONDITIONS)
    module.THEME = "食材、烹饪与营养；简短关键词与网站限定对照"
