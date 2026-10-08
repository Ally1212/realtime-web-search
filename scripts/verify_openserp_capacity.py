"""Read-only verification of complete fixed-configuration hours."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from realtime.campaign_store import CampaignStore
from realtime.config import Config


def verify(run):
    directory = Path("/app/state") / run
    manifest = json.loads((directory / "manifest.json").read_text())
    target = int(manifest.get("target_bodies_per_hour", 1000))
    phase = json.loads((directory / "phases.jsonl").read_text().splitlines()[-1])
    start = datetime.fromisoformat(phase["at"])
    end = min(datetime.now(timezone.utc), datetime.fromisoformat(manifest["deadline"]))
    if manifest.get("finished_at"):
        end = min(end, datetime.fromisoformat(manifest["finished_at"]))
    sql = """
        WITH admitted AS (
            SELECT p.id,min(cp.first_seen) admitted_at,p.source_engines
            FROM pages p JOIN campaign_pages cp ON cp.page_id=p.id
            JOIN wml_continuous_campaigns c ON c.campaign_id=cp.campaign_id
            JOIN wml_continuous_runs r ON r.id=c.run_id
            WHERE r.id=%s AND p.id>r.baseline_page_id AND p.fetched_at>=r.started_at
              AND p.fetched_at<%s AND cp.first_seen<%s AND length(p.content)>=500
            GROUP BY p.id
        ), windows AS (
            SELECT generate_series(%s::timestamptz,%s::timestamptz-interval '1 hour',interval '1 hour') hour_at
        )
        SELECT w.hour_at,count(a.id) new_bodies,
            count(a.id) FILTER (WHERE a.source_engines <> '["google_web"]'::jsonb) non_exclusive_web
        FROM windows w LEFT JOIN admitted a ON a.admitted_at>=w.hour_at
            AND a.admitted_at<w.hour_at+interval '1 hour'
        GROUP BY w.hour_at ORDER BY w.hour_at
    """
    with CampaignStore(Config().database_url).connect() as db:
        windows = [dict(row) for row in db.execute(sql, (run, end, end, start, end))]
        attempts = [dict(row) for row in db.execute(
            "SELECT provider,classification,count(*) n,sum(result_count) urls "
            "FROM google_serp_attempts WHERE created_at>=%s AND created_at<%s GROUP BY 1,2",
            (start, end),
        )]
        hourly_searches = {
            row["hour_at"]: dict(row) for row in db.execute(
                "WITH windows AS (SELECT generate_series(%s::timestamptz,"
                "%s::timestamptz-interval '1 hour',interval '1 hour') hour_at) "
                "SELECT w.hour_at,count(a.id) search_attempts,"
                "count(a.id) FILTER (WHERE a.classification='results') successful_searches,"
                "count(a.id) FILTER (WHERE a.classification='captcha') captcha_attempts,"
                "coalesce(sum(a.result_count),0) result_urls "
                "FROM windows w LEFT JOIN google_serp_attempts a ON a.created_at>=w.hour_at "
                "AND a.created_at<w.hour_at+interval '1 hour' "
                "GROUP BY w.hour_at ORDER BY w.hour_at", (start, end),
            )
        }
    for window in windows:
        searches = hourly_searches[window["hour_at"]]
        window.update({key: value for key, value in searches.items() if key != "hour_at"})
        window["search_success_rate"] = (
            searches["successful_searches"] / searches["search_attempts"]
            if searches["search_attempts"] else None
        )
    last_three = windows[-3:]
    exclusive = bool(attempts) and all(row["provider"] == "openserp" for row in attempts)
    return dict(run=run,phase=phase["phase"],phase_start=start,as_of=end,
                complete_hours=windows,attempts=attempts,
                three_complete_hours_at_target=len(last_three)==3 and exclusive and all(
                    row["new_bodies"]>=target and row["non_exclusive_web"]==0 for row in last_three),
                exclusive_openserp_attempts=exclusive,
                target=target,definition="globally unique new pages, >=500 characters; complete hours anchored to final config phase")


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1] if len(sys.argv)>1 else "openserp-optimization-20261007"),default=str,ensure_ascii=False))
