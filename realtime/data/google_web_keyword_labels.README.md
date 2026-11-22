# Google Web seed snapshot

`google_web_keyword_labels.json` contains English Wikidata labels acquired on
2026-11-18 with the endpoint named in the file. Wikidata structured data is
[CC0](https://www.wikidata.org/wiki/Wikidata:Licensing).

For each category, `class_id` identifies the Wikidata class and each entity retains its Q ID.
The exact response body hash is recorded. The request was:

```sparql
SELECT ?item ?label WHERE {
  ?item wdt:P31 wd:CLASS_ID; rdfs:label ?label .
  FILTER(LANG(?label)="en")
} LIMIT 220
```

The extraction is a bounded seed snapshot, not a representative sample of all
Wikidata. The sampler deterministically shuffles this snapshot and interleaves
categories. Query intent suffixes are maintained in `serp_keyword_pool.py`.
