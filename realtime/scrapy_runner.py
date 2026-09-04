from __future__ import annotations

import argparse
from pathlib import Path

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from .config import Config
from .crawler import FocusedSpider


def repair_jobdir(job_dir: Path) -> int:
    """Remove truncated LIFO queue files left by an interrupted container stop."""
    queue_dir = job_dir / "requests.queue"
    if not queue_dir.exists():
        return 0
    repaired = 0
    for queue_file in queue_dir.rglob("*"):
        if (
            queue_file.is_file()
            and queue_file.name.lstrip("-").isdigit()
            and queue_file.stat().st_size < 4
        ):
            queue_file.unlink()
            repaired += 1
    return repaired


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_id")
    args = parser.parse_args()
    config = Config()
    job_dir = Path("state/jobs") / args.campaign_id
    repaired = repair_jobdir(job_dir)
    if repaired:
        print(f"repaired {repaired} interrupted scheduler queue files", flush=True)
    settings = get_project_settings()
    settings.set("CONCURRENT_REQUESTS", config.crawler_concurrency, priority="cmdline")
    settings.set(
        "CONCURRENT_REQUESTS_PER_DOMAIN", config.crawler_concurrency_per_domain,
        priority="cmdline",
    )
    settings.set("DOWNLOAD_DELAY", config.crawler_download_delay, priority="cmdline")
    settings.set("AUTOTHROTTLE_ENABLED", config.crawler_autothrottle_enabled, priority="cmdline")
    settings.set(
        "AUTOTHROTTLE_TARGET_CONCURRENCY", config.crawler_autothrottle_target,
        priority="cmdline",
    )
    settings.set("RETRY_HTTP_CODES", list(config.crawler_retry_http_codes), priority="cmdline")
    settings.set("ROBOTSTXT_OBEY", config.crawler_obey_robots, priority="cmdline")
    settings.set("DEPTH_LIMIT", config.crawler_depth_limit, priority="cmdline")
    settings.set("USER_AGENT", config.user_agent)
    settings.set("JOBDIR", str(job_dir))
    process = CrawlerProcess(settings)
    process.crawl(FocusedSpider, campaign_id=args.campaign_id)
    process.start()


if __name__ == "__main__":
    main()
