"""One-shot Google comparison export; no campaign scheduling or Whale uploads."""
from __future__ import annotations

import concurrent.futures
import html
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .campaign_store import CampaignStore
from .config import Config
from .discovery import GoogleBlocked, SearchDiscovery, SearchResult
from .fetcher import LiveFetcher, MAX_DOWNLOAD_BYTES, MAX_TEXT_CHARS, normalize_url, relevant_to
from .proxy_pool import ProxyPool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def literal(value: object) -> str:
    text = str(value)
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}text\n{text}\n{fence}\n"


def cell(value: object) -> str:
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", " ")


def quality_warnings(metadata: dict, content: str) -> list[str]:
    warnings = []
    host = (urlsplit(metadata.get("url", "")).hostname or "").lower()
    folded = content.casefold()
    if (host == "youtube.com" or host.endswith(".youtube.com")) and len(content) < 500 and "about press copyright" in folded:
        warnings.append("仅 YouTube 导航/版权文字，未取得视频内容或字幕")
    if len(content) < 1000 and any(marker in folded for marker in (
        "verify you are human", "enable javascript and cookies to continue", "checking your browser",
        "access to this page has been denied", "please enable js and disable any ad blocker",
    )):
        warnings.append("疑似验证/拒绝访问页面，不是目标正文")
    if metadata.get("content_at_limit"):
        warnings.append("正文达到项目 100,000 字符上限，可能截断")
    if content and len(content) < 500 and not warnings:
        warnings.append("短文本，可能只包含摘要、导航或动态页面局部；需人工核对")
    return warnings


def audit_export(directory: Path) -> dict:
    """Annotate saved extraction quality without changing search evidence or refetching."""
    rows = []
    for path in sorted((directory / "documents").glob("*.md")):
        text = path.read_text(encoding="utf-8").split("\n## 质量检查\n", 1)[0]
        metadata = json.JSONDecoder().raw_decode(text.split("```text\n", 1)[1])[0]
        body_section = text.split("## 抽取正文\n\n", 1)[1]
        content = ""
        if body_section.startswith("```"):
            lines = body_section.splitlines()
            content = "\n".join(lines[1:-1])
        warnings = quality_warnings(metadata, content)
        if warnings:
            # Keep the original extracted text and HTTP/extractor status intact.
            section = "\n## 质量检查\n\n" + "\n".join("- " + warning for warning in warnings) + "\n"
            original = text.split("\n## 质量检查\n", 1)[0]
            path.write_text(original + section, encoding="utf-8")
        rows.append((path.name, metadata, content, warnings))
    counts = {
        "urls": len(rows), "extracted_texts": sum(bool(row[2]) for row in rows),
        "youtube_navigation_only": sum(any("YouTube" in warning for warning in row[3]) for row in rows),
        "suspected_challenge": sum(any("疑似验证" in warning for warning in row[3]) for row in rows),
        "short_text_review": sum(any("短文本" in warning for warning in row[3]) for row in rows),
        "possibly_truncated": sum(any("上限" in warning for warning in row[3]) for row in rows),
        "no_extracted_text": sum(not row[2] for row in rows),
    }
    output = "# 抓取质量检查\n\n" + literal(json.dumps(counts, ensure_ascii=False, indent=2))
    output += "\n`success` 只代表项目抽取器取得至少 100 字符文本，不代表完整正文或人工验收通过。"
    output += "以下为启发式检查，未标注的页面也不能保证完整。搜索结果清单保持不变；视频字幕不在本次网页正文采集范围内。\n\n"
    output += "| 文件 | 抓取状态 | 字符数 | 需注意的问题 |\n|---|---|---:|---|\n"
    for name, metadata, content, warnings in rows:
        output += f"| [查看](documents/{name}) | {metadata['status']} | {len(content)} | {cell('；'.join(warnings) or metadata.get('error') or '未发现上述规则匹配的问题')} |\n"
    (directory / "质量检查.md").write_text(output, encoding="utf-8")
    with (directory / "全部正文.md").open("w", encoding="utf-8") as combined:
        combined.write("# 全部网页抓取文本与失败记录\n\n包含导航空壳和局部文本；先读 [质量检查](质量检查.md)。原始搜索顺序见 search-pages。\n\n")
        for name, _, _, _ in rows:
            combined.write((directory / "documents" / name).read_text(encoding="utf-8") + "\n---\n\n")
    readme = directory / "README.md"
    text = readme.read_text(encoding="utf-8")
    if "[质量检查](质量检查.md)" not in text:
        readme.write_text(text + "\n## 正文完整性\n\n请先查看 [质量检查](质量检查.md)：请求/抽取成功不等于取得完整正文，导航空壳、短文本和可能截断的内容已单独标注。\n", encoding="utf-8")
    return counts


def document_markdown(record: dict) -> str:
    document = record.get("document")
    metadata = {key: value for key, value in record.items() if key != "document"}
    if document:
        metadata.update({key: value for key, value in document.items() if key != "content"})
    return "# 网页采集记录\n\n## 元数据\n\n" + literal(
        json.dumps(metadata, ensure_ascii=False, indent=2)
    ) + "\n## 抽取正文\n\n" + (
        literal(document["content"]) if document else "未取得合格正文；原因见元数据。此链接仍保留在搜索结果清单中。\n"
    )


def page_markdown(row: dict, records: dict) -> str:
    metadata = {key: value for key, value in row.items() if key != "results"}
    output = f"# {row['language']} / 第 {row['page']} 页\n\n" + literal(
        json.dumps(metadata, ensure_ascii=False, indent=2)
    )
    output += "\n以下顺序是当前解析器提取的网页链接顺序，不是经验证的桌面 Google 排名；跨页重复不删除。\n\n"
    output += "| 页内顺序 | 搜索标题 | Google 返回的目标链接 | 正文/失败记录 |\n|---:|---|---|---|\n"
    for item in row["results"]:
        record = records[item["normalized_url"]]
        output += f"| {item['position']} | {cell(item['title'])} | {cell(item['url'])} | [查看](../documents/{record['filename']}) |\n"
    return output


def export_summary(report: dict, rows: list[dict], records: dict) -> str:
    good = [item for item in records.values() if item.get("document")]
    hashes = {item["document"]["content_hash"] for item in good}
    header = dict(report, search_pages=len(rows), successful_pages=sum(row["status"] == "success" for row in rows),
                  result_occurrences=sum(len(row["results"]) for row in rows), unique_urls=len(records),
                  downloaded_documents=len(good), unique_content_hashes=len(hashes),
                  failed_or_pending=len(records) - len(good))
    output = "# Google 关键词对照采集\n\n" + literal(json.dumps(header, ensure_ascii=False, indent=2))
    output += (
        "\n## 如何核对\n\n"
        "先打开 `search-pages/` 对应语言的第 1 页，逐条比较 URL，再比较后续页。"
        "中文组和英文组都发送同一个原始查询词，不加引号、不扩词、不加日期条件。"
        "记录的 hl 是界面语言，不是国家限制。未登录你的 Google 账号，未固定出口国家；"
        "代理/备用出口可能变化。手动对照链接只是方便打开，不保证重现采集时的结果。\n\n"
        "采用项目 Google 简化入口及顺序备用、全局限速和代理冷却，不读取搜索缓存。"
        "只记录解析器识别的公开网页链接，不包含完整 Google UI、广告、AI 摘要和截图。"
        "页内顺序不能直接称为桌面排名。请求失败与明确无结果分开标记。\n\n"
        "所有发现的 URL 都有记录；正文使用项目 LiveFetcher 和正文抽取器，直连访问原站，"
        "遵循 robots.txt、公网校验、HTML 类型限制。没有绕过登录、验证码或付费墙。"
        "非 HTML（包括 PDF）、被禁止或失败的页面仅保留链接和原因，不伪装为完整正文。"
        f"下载上限 {MAX_DOWNLOAD_BYTES:,} 字节，正文上限 {MAX_TEXT_CHARS:,} 字符；"
        "正文是抽取文本，不是原网页无损备份。\n\n"
        "为避免影响 SERP 对照，不删除不相关结果；项目相关性判定只作标注。"
        "短词 AI 的现有判定使用子串匹配，可能误命中，不等于人工准确率。"
        "URL 规范化合并抓取，同文哈希重复只标注、不删记录。"
        "字段沿用项目 LiveDocument（URL、标题、正文、摘要、时间、语言、来源、哈希等）。"
        "数据仅写到本导出目录，不创建 Whale 上传任务。\n\n"
        "## 逐页结果\n\n| 语言 | 页码 | 状态 | 链接数 | 用时（秒） | 文件 |\n|---|---:|---|---:|---:|---|\n"
    )
    for row in rows:
        name = f"{row['language']}-{row['page']:02d}.md"
        output += f"| {row['language']} | {row['page']} | {row['status']} | {len(row['results'])} | {row['seconds']} | [查看](search-pages/{name}) |\n"
    output += "\n## 全部唯一链接\n\n| 编号 | 标题 | 抓取状态 | 正文字符数 | 文件 |\n|---:|---|---|---:|---|\n"
    for index, record in enumerate(records.values(), 1):
        doc = record.get("document") or {}
        output += f"| {index} | {cell(record['search_title'])} | {record['status']} | {len(doc.get('content', ''))} | [查看](documents/{record['filename']}) |\n"
    return output


def run_markdown_export(args) -> None:
    languages = tuple(dict.fromkeys(args.languages.split(",")))
    if not args.query.strip() or not 1 <= args.pages <= 11 or not languages or set(languages) - {"zh", "en"}:
        raise ValueError("需要非空查询词、1–11 页，以及 zh/en 语言")
    config = Config()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    # A dedicated empty directory is required; never overwrite a previous export.
    if any(directory.iterdir()):
        raise ValueError("导出目录必须为空，避免覆盖已有数据")
    (directory / "search-pages").mkdir()
    (directory / "documents").mkdir()
    store = CampaignStore(config.database_url, initialize=False)
    pool = ProxyPool(config) if args.profile != "direct" else None
    rows, records = [], {}
    report = {"query": args.query, "languages": languages, "pages_per_language": args.pages,
              "started_at": utc_now(), "finished": False, "search_cache": False,
              "search_proxy_profile": args.profile, "body_proxy_profile": "direct",
              "providers": list(config.google_free_providers), "whale_upload": False}
    started = time.monotonic()

    def save():
        (directory / "README.md").write_text(export_summary(report, rows, records), encoding="utf-8")

    save()
    for language in languages:
        client = SearchDiscovery(
            timeout=20, proxy_pool=pool, proxy_profile=args.profile, language=language,
            providers=config.google_free_providers, searxng_url=config.searxng_url,
            source_slot_acquirer=store.acquire_discovery_slot,
            source_result_recorder=store.record_discovery_result,
            proxy_reserver=store.reserve_google_proxy,
            proxy_result_recorder=store.record_google_proxy_result,
            google_web_initial_rps=config.google_web_initial_rps,
            google_web_max_rps=config.google_web_max_rps,
        )
        try:
            for page in range(1, args.pages + 1):
                tick, at, before = time.monotonic(), utc_now(), len(client.attempts)
                results, error = [], ""
                try:
                    results = client._discover_google_page(args.query, page)
                except GoogleBlocked as exc:
                    error = exc.reason
                params = {"q": args.query, "hl": "zh-CN" if language == "zh" else "en",
                          "start": (page - 1) * 10, "pws": 0}
                row = {"query": args.query, "language": language, "page": page, "requested_at": at,
                       "completed_at": utc_now(), "status": "failed" if error else "success", "error": error,
                       "seconds": round(time.monotonic() - tick, 3), "attempts": client.attempts[before:],
                       "manual_comparison_url": "https://www.google.com/search?" + urlencode(params), "results": []}
                for position, item in enumerate(results, 1):
                    url = normalize_url(item.url)
                    occurrence = {"language": language, "page": page, "position": position, "search_title": item.title}
                    if url not in records:
                        filename = f"{len(records) + 1:03d}-{sha256(url.encode()).hexdigest()[:12]}.md"
                        records[url] = {"requested_url": url, "search_title": item.title, "filename": filename,
                                        "query": args.query, "discovered_at": at, "occurrences": [], "status": "pending"}
                    records[url]["occurrences"].append(occurrence)
                    row["results"].append({"position": position, "url": item.url, "normalized_url": url, "title": item.title})
                    (directory / "documents" / records[url]["filename"]).write_text(document_markdown(records[url]), encoding="utf-8")
                rows.append(row)
                (directory / "search-pages" / f"{language}-{page:02d}.md").write_text(page_markdown(row, records), encoding="utf-8")
                save()
                print(json.dumps({"stage": "search", "language": language, "page": page,
                                  "results": len(results), "error": error, "seconds": row["seconds"]}), flush=True)
        finally:
            client._close_browser()
    report["search_seconds"] = round(time.monotonic() - started, 3)
    fetcher = LiveFetcher(config.user_agent, timeout=20, use_trafilatura=config.trafilatura_enabled)

    def fetch(record):
        tick = time.monotonic()
        item = SearchResult(record["requested_url"], record["search_title"], ("google_web",))
        attempts = []
        for _ in range(2):
            result = fetcher.fetch(item, args.query, record["discovered_at"])
            attempts.append({"status": result.status, "http_status": result.http_status, "error": result.error})
            if result.status != "failed" or result.http_status in {401, 403, 404, 410, 429}:
                break
        document = asdict(result.document) if result.document else None
        return dict(record, status=result.status, http_status=result.http_status, error=result.error,
                    attempts=attempts, seconds=round(time.monotonic() - tick, 3), document=document,
                    project_relevant=bool(document and relevant_to(document["content"], document["title"], (args.query,))),
                    content_at_limit=bool(document and len(document["content"]) >= MAX_TEXT_CHARS))

    hashes = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch, record) for record in records.values()]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            if record["document"]:
                fingerprint = record["document"]["content_hash"]
                record["same_content_as"] = hashes.get(fingerprint)
                hashes.setdefault(fingerprint, record["filename"])
            records[record["requested_url"]] = record
            (directory / "documents" / record["filename"]).write_text(document_markdown(record), encoding="utf-8")
            save()
            print(json.dumps({"stage": "body", "file": record["filename"], "status": record["status"],
                              "characters": len((record["document"] or {}).get("content", ""))}), flush=True)
    report.update(finished=True, finished_at=utc_now(), total_seconds=round(time.monotonic() - started, 3))
    save()
    with (directory / "全部正文.md").open("w", encoding="utf-8") as output:
        output.write("# 全部网页正文与失败记录\n\n含所有唯一链接，未按相关性删除。逐页顺序请查 README.md 和 search-pages。\n\n")
        for record in records.values():
            output.write(document_markdown(record) + "\n---\n\n")
    audit_export(directory)
    print(json.dumps({"finished": True, "unique_urls": len(records), "bodies": len([r for r in records.values() if r.get("document")]),
                      "seconds": report["total_seconds"], "output": str(directory)}), flush=True)
