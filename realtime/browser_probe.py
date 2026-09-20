"""Isolated browser worker; proxy credentials travel over stdin, never argv/logs."""
import json
import sys

from .discovery import GoogleBlocked, SearchDiscovery
from .free_google import GoogleTransport
from .locales import SearchLocale


def main():
    payload = json.load(sys.stdin)
    transport = GoogleTransport(
        int(payload["timeout"]), payload["language"], "",
        search_locale=SearchLocale(
            payload.get("locale_label") or "legacy",
            payload["language"],
            payload.get("hl") or "",
            payload.get("gl") or "",
        ),
        parse_mode=payload.get("parse_mode") or "light",
        time_filter=payload.get("time_filter") or "",
    )
    try:
        rows = transport._browser_page(payload["query"], int(payload["page"]), payload.get("proxy"))
        fields = ("rank", "description", "display_link", "source", "date", "serp_module")
        result = {"results": [
            {"url": row.url, "title": row.title,
             **{field: getattr(row, field) for field in fields}}
            for row in rows
        ], "evidence": dict(transport.last_evidence)}
    except Exception as exc:
        result = {"error": SearchDiscovery._error_code(exc), "captcha": isinstance(exc, GoogleBlocked) and exc.captcha}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    transport.close()


if __name__ == "__main__":
    main()
