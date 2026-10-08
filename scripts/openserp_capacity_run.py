"""Google-only capacity experiment, using the shared durable keyword controller."""
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN = os.getenv("CAPACITY_RUN_ID", "openserp-capacity-20261007")
STATE = Path("/app/state") / RUN


def configure_languages(module, manifest):
    languages = manifest.get("keyword_languages", ["zh", "en"])
    if not isinstance(languages, list) or not languages or any(
        language not in ("zh", "en") for language in languages
    ) or len(set(languages)) != len(languages):
        raise ValueError("keyword_languages must contain unique zh/en language names")
    module.LANGUAGE_INDICES = tuple(0 if language == "zh" else 1 for language in languages)


def controller():
    spec = importlib.util.spec_from_file_location(
        "keyword_controller", STATE / "controller.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((STATE / "manifest.json").read_text())
    module.RUN = RUN
    module.PROVIDER = "openserp"
    module.DIRECTORY = STATE
    module.DEADLINE = datetime.fromisoformat(manifest["deadline"])
    configure_languages(module, manifest)
    module.SOURCES = manifest.get("sources", ["google_web"])
    if not module.SOURCES or set(module.SOURCES) - {"google_web", "google_news", "google_trends", "rss"}:
        raise ValueError("Invalid experiment sources")
    if manifest.get("keyword_variant") == "publishers-v1":
        variant_spec = importlib.util.spec_from_file_location("publisher_keywords", STATE / "publisher_keywords.py")
        variant = importlib.util.module_from_spec(variant_spec)
        variant_spec.loader.exec_module(variant)
        variant.configure(module)
    if manifest.get("keyword_variant") == "world-v1":
        variant_spec = importlib.util.spec_from_file_location("world_keywords", STATE / "world_keywords.py")
        variant = importlib.util.module_from_spec(variant_spec)
        variant_spec.loader.exec_module(variant)
        variant.configure(module)
    offset = int(manifest.get("keyword_offset", 0))
    module.KEYWORD_OFFSET = offset
    original_keyword = module.keyword
    # Rotate within each catalog so its last entries don't become dated
    # searches prematurely, and saved base catalogs contain real queries.
    def offset_keyword(index, language, cycle_date):
        cycle, position = divmod(index, module.CATALOG_SIZE)
        shifted = cycle * module.CATALOG_SIZE + (position + offset) % module.CATALOG_SIZE
        return original_keyword(shifted, language, cycle_date)

    module.keyword = offset_keyword
    return module



def measure_first_hour(module, result):
    """Persist an exact run-anchored hour, separate from provisional rate estimates."""
    from datetime import timedelta
    from realtime.campaign_store import CampaignStore
    from realtime.config import Config
    existing = STATE / "first-hour-audit.json"
    if existing.exists():
        completed = json.loads(existing.read_text())
        if completed.get("full_hour_observed"):
            result["first_hour"] = {k:completed[k] for k in ("full_hour_observed", "new_qualified_bodies", "bodies_per_hour", "measured_until")}
            return completed
    start = result["started_at"]
    end = min(datetime.now(timezone.utc), start + timedelta(hours=1), module.DEADLINE)
    with CampaignStore(Config().database_url).connect() as db:
        run = db.execute("SELECT baseline_page_id FROM wml_continuous_runs WHERE id=%s", (RUN,)).fetchone()
        admitted = ("WITH admitted AS (SELECT p.id,p.source_engines,min(cp.first_seen) admitted_at FROM pages p "
                    "JOIN campaign_pages cp ON cp.page_id=p.id JOIN wml_continuous_campaigns c ON c.campaign_id=cp.campaign_id "
                    "WHERE c.run_id=%s AND p.id>%s AND p.fetched_at>=%s AND p.fetched_at<%s "
                    "AND cp.first_seen>=%s AND cp.first_seen<%s AND length(p.content)>=500 GROUP BY p.id) ")
        params = (RUN, run["baseline_page_id"], start, end, start, end)
        sources = db.execute(admitted + "SELECT source_engines,count(*) n FROM admitted GROUP BY 1 ORDER BY count(*) DESC", params).fetchall()
        buckets = db.execute(admitted + "SELECT floor(extract(epoch FROM (admitted_at-%s))/300)::integer bucket,count(*) n FROM admitted GROUP BY 1 ORDER BY 1", (*params, start)).fetchall()
        searches = db.execute("SELECT provider,page,classification,count(*) attempts,sum(result_count) links "
                              "FROM google_serp_attempts WHERE created_at>=%s AND created_at<%s GROUP BY 1,2,3 ORDER BY 1,2,3", (start, end)).fetchall()
    count = sum(row["n"] for row in sources)
    report = dict(run=RUN, started_at=start, measured_until=end, full_hour_observed=(end-start).total_seconds()>=3600,
                  elapsed_seconds=round((end-start).total_seconds(),1), new_qualified_bodies=count,
                  bodies_per_hour=count if (end-start).total_seconds()>=3600 else None,
                  source_combinations=sources, five_minute_buckets=buckets, searches_by_page=searches,
                  definition="Globally unique new pages >=500 characters, fetched and admitted within this exact run-anchored first hour; source combinations are disjoint")
    module.save("first-hour-audit.json", report)
    result["first_hour"] = {k:report[k] for k in ("full_hour_observed", "new_qualified_bodies", "bodies_per_hour", "measured_until")}
    return report



def measure_run_hours(module, result):
    from datetime import timedelta
    from realtime.campaign_store import CampaignStore
    from realtime.config import Config
    start = result["started_at"]
    end = min(datetime.now(timezone.utc), module.DEADLINE)
    with CampaignStore(Config().database_url).connect() as db:
        rows = db.execute(
            "WITH admitted AS (SELECT p.id,min(cp.first_seen) t FROM pages p "
            "JOIN campaign_pages cp ON cp.page_id=p.id JOIN wml_continuous_campaigns c ON c.campaign_id=cp.campaign_id "
            "JOIN wml_continuous_runs r ON r.id=c.run_id WHERE r.id=%s AND p.id>r.baseline_page_id "
            "AND p.fetched_at>=r.started_at AND p.fetched_at<%s AND cp.first_seen>=%s "
            "AND cp.first_seen<%s AND length(p.content)>=500 GROUP BY p.id) "
            "SELECT floor(extract(epoch FROM (t-%s))/3600)::integer bucket,count(*) n FROM admitted GROUP BY 1 ORDER BY 1",
            (RUN,end,start,end,start),
        ).fetchall()
    counts = {row["bucket"]:row["n"] for row in rows}
    hours = []
    bucket = 0
    while start+timedelta(hours=bucket)<end:
        left = start+timedelta(hours=bucket)
        right = min(left+timedelta(hours=1),end)
        hours.append(dict(start=left,end=right,complete=(right-left).total_seconds()>=3600,new_qualified_bodies=counts.get(bucket,0)))
        bucket += 1
    completed = [row["new_qualified_bodies"] for row in hours if row["complete"]]
    best = max(completed) if completed else None
    module.save("run-hours-audit.json",dict(run=RUN,as_of=end,hours=hours,best_complete_hour_bodies=best,
                                          target=2000,target_met=best is not None and best>=2000))
    result["best_complete_hour_bodies"] = best

def main(action="tick"):
    module = controller()
    original_save = module.save

    def defer_report(name, value):
        if name not in ("report.json", "final-report.json"):
            original_save(name, value)

    # The controller's intermediate report lacks the phase fields. Publish
    # only after augmentation so readers always see a complete snapshot.
    module.save = defer_report
    try:
        result = module.finish() if action == "finish" else module.tick()
    finally:
        module.save = original_save
    if "started_at" in result:
        end = min(datetime.now(timezone.utc), module.DEADLINE)
        elapsed = max(0, (end - result["started_at"]).total_seconds())
        result["elapsed_seconds"] = round(elapsed, 1)
        result["full_hour_observed"] = elapsed >= 3600
        result["mean_bodies_per_hour"] = round(result["new_qualified_bodies"] * 3600 / elapsed, 1) if elapsed else None
        result["rate_is_short_window_projection"] = elapsed < 3600
        phases = STATE / "phases.jsonl"
        if phases.exists():
            target = int(json.loads((STATE / "manifest.json").read_text()).get("target_bodies_per_hour", 1000))
            phase = json.loads(phases.read_text().splitlines()[-1])
            phase_start = datetime.fromisoformat(phase["at"])
            phase_seconds = max(0, (end - phase_start).total_seconds())
            from realtime.campaign_store import CampaignStore
            from realtime.config import Config
            with CampaignStore(Config().database_url).connect() as db:
                count = db.execute(
                    "WITH admitted AS (SELECT p.id,min(cp.first_seen) t FROM pages p "
                    "JOIN campaign_pages cp ON cp.page_id=p.id JOIN wml_continuous_campaigns c "
                    "ON c.campaign_id=cp.campaign_id JOIN wml_continuous_runs r ON r.id=c.run_id "
                    "WHERE r.id=%s AND p.id>r.baseline_page_id AND p.fetched_at>=r.started_at "
                    "AND p.fetched_at<%s AND cp.first_seen<%s AND length(p.content)>=500 GROUP BY p.id) "
                    "SELECT count(*) n FROM admitted WHERE t>=greatest(%s,%s-interval '1 hour')",
                    (RUN, end, end, phase_start, end),
                ).fetchone()["n"]
            result.update(current_phase=phase["phase"], phase_elapsed_seconds=round(phase_seconds, 1),
                          phase_full_hour_observed=phase_seconds >= 3600,
                          phase_last_hour_bodies=count,
                          phase_target_bodies_per_hour=target,
                          phase_last_hour_target_met=phase_seconds >= 3600 and count >= target)
        if json.loads((STATE / "manifest.json").read_text()).get("measure_first_hour"):
            measure_first_hour(module, result)
            measure_run_hours(module, result)
        module.save("report.json", result)
        if result["status"] == "finished":
            module.save("final-report.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("hours", "searches")}, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tick")
