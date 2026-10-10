"""Deterministic broad English queries for SERP collection."""
TOPICS = '''artificial intelligence|machine learning|robotics|semiconductors|quantum computing|cybersecurity|cloud computing|data centers|software engineering|open source|electric vehicles|battery technology|solar energy|wind power|hydrogen energy|nuclear energy|energy storage|power grids|climate change|carbon capture|water conservation|ocean science|space exploration|astronomy|satellite technology|biotechnology|genetics|neuroscience|public health|medical research|mental health|nutrition science|fitness|sleep science|aging research|pharmaceuticals|vaccines|agriculture|food technology|sustainable farming|urban planning|architecture|construction|real estate|transportation|railways|aviation|shipping|logistics|supply chains|manufacturing|industrial automation|materials science|nanotechnology|chemistry|physics|mathematics|education|online learning|universities|vocational training|labor markets|remote work|entrepreneurship|small business|venture capital|banking|insurance|financial technology|digital payments|ecommerce|retail|consumer electronics|mobile technology|telecommunications|internet infrastructure|privacy|digital media|journalism|film industry|music industry|video games|sports science|tourism|hospitality|national parks|wildlife conservation|biodiversity|forestry|mining|critical minerals|economic policy|international trade|inflation|renewable infrastructure|disaster preparedness|earthquake research|weather forecasting|geography|archaeology'''.split('|')
FACETS = '''research|innovation|market outlook|industry analysis|investment|regulation|policy|technology|case study|challenges|opportunities|economic impact|environmental impact|safety|standards|infrastructure|development|applications|best practices|statistics|trends|education|employment|costs|projects|sustainability|performance|adoption|history|future'''.split('|')
REGIONS = '''United States|United Kingdom|Canada|Australia|Germany|France|Italy|Spain|Netherlands|Sweden|Norway|Denmark|Finland|Switzerland|Austria|Ireland|Poland|Portugal|Japan|South Korea|Singapore|India|China|Taiwan|Hong Kong|Indonesia|Malaysia|Thailand|Vietnam|Philippines|New Zealand|Brazil|Mexico|Argentina|Chile|South Africa|Kenya|Nigeria|Egypt|Morocco'''.split('|')

CATALOG_SIZE = len(TOPICS) * len(FACETS) * len(REGIONS)

def keyword(index: int, date: str) -> str:
    cycle, offset = divmod(index, CATALOG_SIZE)
    position = offset * 7919 % CATALOG_SIZE
    position, region = divmod(position, len(REGIONS))
    topic, facet = divmod(position, len(FACETS))
    phrase = f"{TOPICS[topic]} {FACETS[facet]} {REGIONS[region]}"
    return phrase + (f" after:{date}" if cycle else "")
