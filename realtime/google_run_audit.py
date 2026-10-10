"""Read-only verification of immutable body admissions in a fixed run window."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def read_first_body_window(db, run: str, start: datetime, end: datetime,
                           expected_bodies: int, expected_missing: int) -> dict[str, Any]:
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
        raise ValueError('body window audit requires a read-only transaction')
    baseline = db.execute('SELECT baseline_page_id FROM wml_continuous_runs WHERE id=%s',
                          (run,)).fetchone()['baseline_page_id']
    first = (
        'WITH first_association AS (SELECT cp.page_id,min(cp.first_seen) first_at '
        'FROM campaign_pages cp JOIN wml_continuous_campaigns c ON c.campaign_id=cp.campaign_id '
        'WHERE c.run_id=%s AND cp.page_id>%s GROUP BY cp.page_id), '
        'admitted AS (SELECT f.page_id,a.original_content_hash,a.original_body_chars, '
        'a.original_source_engines,a.original_http_status,a.original_fetched_at, '
        'a.page_id IS NULL missing,p.content_hash,p.content, '
        '(a.original_body_chars>=500 AND a.original_fetched_at>=%s '
        'AND a.original_fetched_at<%s) qualified FROM first_association f '
        'JOIN pages p ON p.id=f.page_id LEFT JOIN page_admission_audit a ON a.page_id=f.page_id '
        'WHERE f.first_at>=%s AND f.first_at<%s) '
    )
    params = (run, baseline, start, end, start, end)
    counts = db.execute(first +
        'SELECT count(*) associated_pages,count(*) FILTER(WHERE missing) missing_admission_rows, '
        'count(*) FILTER(WHERE qualified) qualified_bodies, '
        'count(DISTINCT original_content_hash) FILTER(WHERE qualified) distinct_original_hashes, '
        'min(original_body_chars) FILTER(WHERE qualified) min_original_body_chars, '
        'max(original_body_chars) FILTER(WHERE qualified) max_original_body_chars, '
        "count(*) FILTER(WHERE qualified AND content_hash<>encode(sha256(convert_to(content,'UTF8')),'hex')) current_body_hash_mismatches, "
        'count(*) FILTER(WHERE qualified AND NOT EXISTS(SELECT 1 FROM page_content_hashes h '
        'WHERE h.content_hash=admitted.content_hash AND h.page_id=admitted.page_id)) current_hash_owner_missing, '
        'count(*) FILTER(WHERE qualified AND original_http_status<>200) original_non200_bodies '
        'FROM admitted', params,
    ).fetchone()
    duplicates = db.execute(first +
        'SELECT count(*) duplicate_original_hash_groups FROM '
        '(SELECT a.original_content_hash FROM page_admission_audit a JOIN '
        '(SELECT DISTINCT original_content_hash FROM admitted WHERE qualified) h '
        'USING(original_content_hash) GROUP BY a.original_content_hash HAVING count(*)>1) d', params,
    ).fetchone()['duplicate_original_hash_groups']
    sources = db.execute(first +
        'SELECT original_source_engines source_engines,count(*) n FROM admitted WHERE qualified '
        'GROUP BY original_source_engines', params,
    ).fetchall()
    checks = dict(qualified_count_matches=counts['qualified_bodies'] == expected_bodies,
                  missing_count_matches=counts['missing_admission_rows'] == expected_missing,
                  no_missing_admissions=counts['missing_admission_rows'] == 0,
                  original_hashes_unique=counts['qualified_bodies'] == counts['distinct_original_hashes'],
                  retained_global_original_hashes_unique=duplicates == 0,
                  current_body_hash_consistent=counts['current_body_hash_mismatches'] == 0,
                  current_hash_owners_present=counts['current_hash_owner_missing'] == 0)
    return dict(database_read_only=True, counts=dict(counts),
                retained_duplicate_original_hash_groups=duplicates, source_combinations=sources,
                checks=checks, all_checks_passed=all(checks.values()),
                definition='First run association above the run page-id baseline, immutable first-admission '
                           'body >=500 chars and original fetch inside the exact window. Current hash and retained '
                           'global original-hash checks are separate from first-admission counts. '
                           'This does not reconstruct missing historical versions or prove semantic article quality.')
