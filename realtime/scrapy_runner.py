from __future__ import annotations

import argparse

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from .config import Config
from .crawler import FocusedSpider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_id")
    args = parser.parse_args()
    config = Config()
    settings = get_project_settings()
    settings.set("CONCURRENT_REQUESTS", config.crawler_concurrency, priority="cmdline")
    settings.set(
        "CONCURRENT_REQUESTS_PER_DOMAIN", config.crawler_concurrency_per_domain,
        priority="cmdline",
    )
    settings.set("DOWNLOAD_DELAY", config.crawler_download_delay, priority="cmdline")
    settings.set(
        "AUTOTHROTTLE_TARGET_CONCURRENCY", config.crawler_autothrottle_target,
        priority="cmdline",
    )
    settings.set("DEPTH_LIMIT", config.crawler_depth_limit, priority="cmdline")
    settings.set("USER_AGENT", config.user_agent)
    settings.set("JOBDIR", f"state/jobs/{args.campaign_id}")
    process = CrawlerProcess(settings)
    process.crawl(FocusedSpider, campaign_id=args.campaign_id)
    process.start()


if __name__ == "__main__":
    main()
