"""Isolated browser worker; proxy credentials travel over stdin, never argv/logs."""
import json
import sys

from .discovery import GoogleBlocked, SearchDiscovery
from .free_google import GoogleTransport


def main():
    payload = json.load(sys.stdin)
    transport = GoogleTransport(
        int(payload["timeout"]), payload["language"], "",
        google_serp_save_html=bool(payload.get("save_html", False)),
        google_serp_evidence_dir=payload.get("evidence_dir", "state/serp-evidence"),
    )
    try:
        rows = transport._browser_page(payload["query"], int(payload["page"]), payload.get("proxy"))
        result = {"results": [{"url": row.url, "title": row.title} for row in rows]}
    except Exception as exc:
        result = {"error": SearchDiscovery._error_code(exc), "captcha": isinstance(exc, GoogleBlocked) and exc.captcha}
    result["evidence"] = transport.last_evidence
    print(json.dumps(result, ensure_ascii=False), flush=True)
    transport.close()


if __name__ == "__main__":
    main()
