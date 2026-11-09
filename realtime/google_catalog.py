"""Generated PRD catalog, not a claim of implemented endpoints or approved sources."""

SOURCE_SHA256 = '3cbe48ddbdf990fbba5543a3d2cdfdd7c80d437c757f490f403244871b99b8e1'

API_CATALOG = [
  {
    "id": "GO-API-001",
    "description": "source/service/entity/field/use/tenant\u2192\u53ef\u5f97\u8303\u56f4\u3001\u51c6\u5165\u3001\u4fdd\u5b58/\u5206\u53d1/\u671f\u9650\u3001\u539f\u56e0",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/capabilities"
      }
    ]
  },
  {
    "id": "GO-API-002",
    "description": "source\u3001country\u3001language\u2192\u652f\u6301\u9879/\u5750\u6807\u7cbe\u5ea6\uff0c\u4e0d\u5047\u8bbe\u6e90\u95f4location code\u901a\u7528",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/markets"
      },
      {
        "method": "GET",
        "path": "/locations"
      }
    ]
  },
  {
    "id": "GO-API-003",
    "description": "text/topic/intent/collection_version\u2192\u539f\u8bcd\u3001\u5f52\u4e00\u5316\u3001alias\u4e0e\u6307\u6807\u5f15\u7528",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/keywords"
      },
      {
        "method": "GET",
        "path": "/keywords/{id}"
      }
    ]
  },
  {
    "id": "GO-API-004",
    "description": "source/topic/brand\u2192\u5173\u7cfb\u3001\u65b9\u6cd5\u3001confidence\u4e0e\u8bc1\u636e",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/topics"
      },
      {
        "method": "GET",
        "path": "/topics/{id}/relations"
      }
    ]
  },
  {
    "id": "GO-API-005",
    "description": "query/market/location/device/surface\u2192\u914d\u7f6e\u4e0ehash\u7248\u672c",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/query-variants"
      }
    ]
  },
  {
    "id": "GO-API-006",
    "description": "variant\u3001scheduled/observed windows\u3001status\u2192run\u3001\u7f13\u5b58/\u8d28\u91cf/\u8d39\u7528",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/observations"
      },
      {
        "method": "GET",
        "path": "/observations/{id}"
      }
    ]
  },
  {
    "id": "GO-API-007",
    "description": "result_type/depth\u2192\u987a\u5e8fitem\u3001\u7236block\u3001URL\u4e0e\u6e90rank",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/observations/{id}/items"
      }
    ]
  },
  {
    "id": "GO-API-008",
    "description": "query/variant/target/source/window\u2192rank\u53d8\u5316\u3001\u672a\u5165\u6df1\u5ea6/\u5931\u8d25/\u5207\u6e90\u65ad\u70b9",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/serp-history"
      }
    ]
  },
  {
    "id": "GO-API-009",
    "description": "feature/target/variant/window\u2192\u51fa\u73b0\u3001\u62e5\u6709\u3001\u672a\u77e5\u548c\u8bc1\u636e",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/serp-features"
      }
    ]
  },
  {
    "id": "GO-API-010",
    "description": "domain\u3001cohort/scope\u2192\u5b9e\u4f53\u53ca\u53ef\u6bd4\u805a\u5408\uff0c\u4e0d\u9ed8\u8ba4\u771f\u5b9e\u6d41\u91cf",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/domains"
      },
      {
        "method": "GET",
        "path": "/domains/{id}"
      }
    ]
  },
  {
    "id": "GO-API-011",
    "description": "path/query/cohort\u2192\u9875\u9762/URL\u4efd\u989d\u3001\u6392\u540d\u3001feature\u4e0e\u6765\u6e90",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/domains/{id}/pages"
      }
    ]
  },
  {
    "id": "GO-API-012",
    "description": "universe/version\u3001metric/surface/window\u2192\u6392\u540d\u3001\u8986\u76d6\u4e0e\u6743\u91cd\u5b9a\u4e49",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/rankings"
      }
    ]
  },
  {
    "id": "GO-API-013",
    "description": "domains/brands\u3001cohort\u3001method\u2192\u56fa\u5b9a\u96c6\u5408\u5dee\u5f02/\u4e2d\u4f4d/\u4efd\u989d",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/comparisons"
      }
    ]
  },
  {
    "id": "GO-API-014",
    "description": "terms/close_variant_group/geo/network/source/date\u2192\u5706\u6574/\u4f30\u7b97\u91cf\u548c\u5e7f\u544a\u7ade\u4e89",
    "phase": "P0\u4f9b\u5e94\u5546/P1\u5b98\u65b9",
    "operations": [
      {
        "method": "GET",
        "path": "/keyword-metrics"
      }
    ]
  },
  {
    "id": "GO-API-015",
    "description": "term/topic\u3001geo\u3001window/granularity/scale_policy\u2192\u65f6\u5e8f\u3001\u4fee\u8ba2\u3001\u4f4e\u91cf\u72b6\u6001",
    "phase": "P0\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/trends/series"
      }
    ]
  },
  {
    "id": "GO-API-016",
    "description": "series/query\u3001top/rising/window\u2192\u5217\u8868\u3001breakout\u3001\u5206\u6bcd/\u6765\u6e90",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/trends/related"
      }
    ]
  },
  {
    "id": "GO-API-017",
    "description": "topic/query/market/window\u2192\u5217\u8868occurrences\u3001publisher/URL/\u65f6\u95f4",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/news/observations"
      }
    ]
  },
  {
    "id": "GO-API-018",
    "description": "topic/cluster_version/window\u2192\u4e8b\u4ef6\u7c07\u3001\u72ec\u7acb\u6587\u7ae0\u3001\u8f6c\u8f7d\u4e0e\u65b9\u6cd5",
    "phase": "P0\u57fa\u7840",
    "operations": [
      {
        "method": "GET",
        "path": "/news/events"
      }
    ]
  },
  {
    "id": "GO-API-019",
    "description": "fields/version\u2192\u8bb8\u53ef\u5185\u6b63\u6587\u6216\u6458\u8981\u3001\u6765\u6e90\u3001rights\u3001Whale\u5f15\u7528",
    "phase": "P0\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/articles/{id}"
      }
    ]
  },
  {
    "id": "GO-API-020",
    "description": "prompt/variant/channel/window\u2192\u56de\u7b54/\u53ef\u89c1\u6027\u72b6\u6001\u3001\u914d\u7f6e\u3001\u8bb8\u53ef\u5185\u8bc1\u636e",
    "phase": "P0 AIO/P1 Mode",
    "operations": [
      {
        "method": "GET",
        "path": "/ai/answers"
      },
      {
        "method": "GET",
        "path": "/ai/answers/{id}"
      }
    ]
  },
  {
    "id": "GO-API-021",
    "description": "answer/domain/brand/surface\u2192\u53ef\u89c1\u5f15\u7528\u3001URL\u5173\u7cfb\u4e0e\u8bc1\u636e",
    "phase": "P0\u57fa\u7840",
    "operations": [
      {
        "method": "GET",
        "path": "/ai/citations"
      }
    ]
  },
  {
    "id": "GO-API-022",
    "description": "prompt cohort\u3001brand set\u3001channel/window\u2192mention/citation/SoV/\u4f4d\u7f6e\u4e0e\u6709\u6548\u5206\u6bcd",
    "phase": "P0\u63cf\u8ff0/P1\u91cd\u590d",
    "operations": [
      {
        "method": "GET",
        "path": "/ai/visibility"
      }
    ]
  },
  {
    "id": "GO-API-023",
    "description": "source/use/asset\u2192\u5730\u56fe\u7f51\u683c\u3001\u8bc4\u8bba\u548c\u5c55\u793a\u80fd\u529b\u51c6\u5165\uff0c\u4e0d\u8fd4\u56de\u53d7\u9650\u5185\u5bb9",
    "phase": "P0\u4fe1\u606f/P1\u6269\u5c55",
    "operations": [
      {
        "method": "GET",
        "path": "/local/capabilities"
      }
    ]
  },
  {
    "id": "GO-API-024",
    "description": "grid/version/window\u2192\u83b7\u51c6\u4f9b\u5e94\u5546\u9010\u70b9\u7ed3\u679c\u3001\u7f3a\u6d4b\u4e0e\u6210\u672c",
    "phase": "P1\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/local/grids"
      },
      {
        "method": "GET",
        "path": "/local/grids/{id}/observations"
      }
    ]
  },
  {
    "id": "GO-API-025",
    "description": "\u83b7\u51c6place ID/\u5fc5\u8981\u94fe\u63a5/\u6765\u6e90\u4e0e\u7528\u9014\u72b6\u6001",
    "phase": "P1\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/places/{id}/reference"
      }
    ]
  },
  {
    "id": "GO-API-026",
    "description": "\u72ec\u7acb\u83b7\u51c6source/rights/place/sample\u2192\u4e3b\u9898/\u6bd4\u4f8b/\u8bc1\u636e\uff1b\u9ed8\u8ba4disabled",
    "phase": "P1\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/local/review-insights"
      }
    ]
  },
  {
    "id": "GO-API-027",
    "description": "asset/template/date/dataState/aggregation\u2192top_rows\u6216bulk\u540cgrain\u6570\u636e\u3001\u9690\u79c1/\u5b8c\u6574\u6027",
    "phase": "P0\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/search-console/reports"
      }
    ]
  },
  {
    "id": "GO-API-028",
    "description": "authorized property/sample URL\u2192\u7d22\u5f15\u7248\u672c\u3001\u65f6\u95f4\u3001\u95ee\u9898\u4e0e\u8bfb\u53d6\u914d\u989d",
    "phase": "P1",
    "operations": [
      {
        "method": "GET",
        "path": "/search-console/diagnostics"
      }
    ]
  },
  {
    "id": "GO-API-029",
    "description": "property/template/date/scope\u2192GA4\u62a5\u544a\u3001metadata\u548ctoken",
    "phase": "P1",
    "operations": [
      {
        "method": "GET",
        "path": "/analytics/reports"
      }
    ]
  },
  {
    "id": "GO-API-030",
    "description": "property/report/BQ compatible scope\u2192\u5dee\u5f02\u5206\u89e3\uff0c\u4e0d\u5f3a\u5236\u96f6\u5dee",
    "phase": "P1",
    "operations": [
      {
        "method": "GET",
        "path": "/analytics/reconciliations"
      }
    ]
  },
  {
    "id": "GO-API-031",
    "description": "\u4ec5\u83b7\u51c6\u5185\u90e8\u5206\u6790/\u62a5\u544a\u6a21\u5f0f\uff0capproved plan/geo/network/window\u2192\u8bb8\u53ef\u5185\u89c4\u5212\u7ed3\u679c\uff1b\u4e3b\u4f53\u9879\u76ee\u51c6\u5165\u7ed1\u5b9a\uff0c\u4e0d\u505a\u4efb\u610f\u6e90\u65b9\u6cd5\u4ee3\u7406",
    "phase": "P1\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/ads/keyword-plans"
      }
    ]
  },
  {
    "id": "GO-API-032",
    "description": "\u4ec5\u83b7\u51c6\u4e3b\u4f53/\u62a5\u544a\u670d\u52a1\uff0ccustomer/template/window/attribution/currency\u2192\u8bb8\u53ef\u5185\u5b9e\u7ee9/\u4fee\u8ba2\uff1b\u8de8\u4e3b\u4f53\u5171\u4eab\u51c6\u5165\u4e0e\u7eaf\u4ee3\u7406\u8d1f\u4f8b\u62d2\u7edd",
    "phase": "P1\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/ads/reports"
      }
    ]
  },
  {
    "id": "GO-API-033",
    "description": "authorized merchant/template/version/window\u2192\u6279\u51c6\u62a5\u8868\u4e0e\u5b57\u6bb5",
    "phase": "P1\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/merchant/reports"
      }
    ]
  },
  {
    "id": "GO-API-034",
    "description": "brand/version/cohort/window\u2192\u9700\u6c42\u3001\u641c\u7d22/\u65b0\u95fb/AI\u5206\u8f68\u4e0e\u8bc1\u636e",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/insights/brands"
      }
    ]
  },
  {
    "id": "GO-API-035",
    "description": "keyword/topic/target/cohort/method\u2192\u89e3\u91ca\u6027\u673a\u4f1a\u8bc4\u5206\u3001\u6210\u672c\u4e0e\u4e0d\u53ef\u5224\u5b9a\u9879",
    "phase": "P0\u57fa\u7840/P1\u6df1\u5316",
    "operations": [
      {
        "method": "GET",
        "path": "/insights/opportunities"
      }
    ]
  },
  {
    "id": "GO-API-036",
    "description": "monitor/window/rule/model\u2192\u53d8\u5316\u3001\u57fa\u7ebf\u3001\u6570\u636e/\u4e1a\u52a1\u7c7b\u522b\u4e0e\u8bc1\u636e",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/insights/anomalies"
      }
    ]
  },
  {
    "id": "GO-API-037",
    "description": "metric/version\u3001evidence/manifest\u2192\u516c\u5f0f\u3001\u5206\u6bcd\u3001\u53ef\u52a0\u6027\u3001\u8bb8\u53ef\u5185\u539f\u8bc1\u636e",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/metric-definitions"
      },
      {
        "method": "GET",
        "path": "/evidence/{id}"
      }
    ]
  },
  {
    "id": "GO-API-038",
    "description": "\u8bcd/prompt/\u57df/\u6c60\u3001source\u3001\u9891\u7387/\u9884\u7b97/\u89c4\u5219\uff1bIf-Match\u7248\u672c\u53d8\u66f4",
    "phase": "P0",
    "operations": [
      {
        "method": "POST",
        "path": "/monitors"
      },
      {
        "method": "GET",
        "path": "/monitors"
      },
      {
        "method": "GET",
        "path": "/monitors/{id}"
      },
      {
        "method": "PATCH",
        "path": "/monitors/{id}"
      }
    ]
  },
  {
    "id": "GO-API-039",
    "description": "\u6536\u85cf/\u79fb\u51fa\uff0c\u4e0d\u5220\u9664\u6e90\u4e8b\u5b9e",
    "phase": "P0",
    "operations": [
      {
        "method": "POST",
        "path": "/watchlists"
      },
      {
        "method": "GET",
        "path": "/watchlists"
      },
      {
        "method": "DELETE",
        "path": "/watchlists/{id}/items/{entity_id}"
      }
    ]
  },
  {
    "id": "GO-API-040",
    "description": "manifest\u3001query/method versions\u2192\u5f02\u6b65\u5206\u6790/\u90e8\u5206\u5931\u8d25/\u53d6\u6d88",
    "phase": "P0",
    "operations": [
      {
        "method": "POST",
        "path": "/analysis-jobs"
      },
      {
        "method": "GET",
        "path": "/analysis-jobs/{id}"
      }
    ]
  },
  {
    "id": "GO-API-041",
    "description": "fields/format/manifest\u2192\u8bb8\u53ef\u5185\u51bb\u7ed3\u7ed3\u679c\uff0c\u751f\u6210/\u4e0b\u8f7d\u590d\u6838",
    "phase": "P0",
    "operations": [
      {
        "method": "POST",
        "path": "/exports"
      },
      {
        "method": "GET",
        "path": "/exports/{id}"
      }
    ]
  },
  {
    "id": "GO-API-042",
    "description": "approved source/variant/max units\u2192\u6309\u9700\u89c2\u5bdf\u3001\u9884\u7b97/\u914d\u989d/\u7ed3\u679c\u72b6\u6001",
    "phase": "P1\u6761\u4ef6",
    "operations": [
      {
        "method": "POST",
        "path": "/live-queries"
      },
      {
        "method": "GET",
        "path": "/live-queries/{id}"
      }
    ]
  },
  {
    "id": "GO-API-043",
    "description": "approved query/manifest\u2192\u786e\u5b9a\u6027/AI\u62a5\u544a\u3001\u8bc1\u636e\u3001\u53cd\u4f8b\u4e0e\u9650\u5236",
    "phase": "P0\u786e\u5b9a\u6027/P2 AI",
    "operations": [
      {
        "method": "POST",
        "path": "/reports"
      },
      {
        "method": "GET",
        "path": "/reports/{id}"
      }
    ]
  },
  {
    "id": "GO-API-044",
    "description": "\u57df\u89c2\u5bdf/\u544a\u8b66\u81f3\u5c11\u4e00\u6b21\u6295\u9012\uff0c\u539f\u59cb\u5185\u5bb9\u4ecd\u8d70Whale\u53d8\u66f4",
    "phase": "P1",
    "operations": [
      {
        "method": "GET",
        "path": "/changes"
      },
      {
        "method": "POST",
        "path": "/subscriptions"
      },
      {
        "method": "GET",
        "path": "/subscriptions"
      },
      {
        "method": "GET",
        "path": "/subscriptions/{id}"
      },
      {
        "method": "DELETE",
        "path": "/subscriptions/{id}"
      }
    ]
  },
  {
    "id": "GO-API-045",
    "description": "\u7ba1\u7406\u89d2\u8272\u8bfb\u8ba1\u5212\u8986\u76d6/\u7f13\u5b58/\u9519\u8bef/\u8d44\u6e90/\u8d39\u7528/\u6e90\u989d\u5ea6",
    "phase": "P0",
    "operations": [
      {
        "method": "GET",
        "path": "/quality"
      },
      {
        "method": "GET",
        "path": "/usage"
      }
    ]
  },
  {
    "id": "GO-API-046",
    "description": "\u83b7\u51c6\u8d44\u4ea7\u3001service/project reference\u3001role/scope\u3001expiry/status\uff1b\u4e0d\u8fd4\u56detoken",
    "phase": "P0\u6761\u4ef6",
    "operations": [
      {
        "method": "GET",
        "path": "/connections"
      }
    ]
  }
]

METRIC_CATALOG = [
  {
    "id": "GO-MET-001",
    "version": "1",
    "definition": "organic_position=\u6307\u5b9arun\u5185\u76ee\u6807URL\u6216\u57df\u540d\u9996\u6b21\u81ea\u7136\u7ed3\u679c\u5e8f\u53f7\uff1b\u4fdd\u7559\u5168\u90e8occurrences",
    "limitations": "\u672a\u8fdbtopN\u4e3aoutside_observed_depth\uff0c\u4e0d\u586bN+1\u6216101\uff1b\u8de8device/geo\u4e0d\u6df7\u5e73\u5747"
  },
  {
    "id": "GO-MET-002",
    "version": "1",
    "definition": "observed_topK_share=\u6210\u529f\u4e14\u6ee1\u8db3\u6df1\u5ea6\u7684run\u4e2d\u76ee\u6807\u8fdb\u524dK\u7684\u6b21\u6570/\u6709\u6548run\u6570",
    "limitations": "planned/completed/eligible/failed\u540c\u65f6\u516c\u5f00\uff1b\u4e0d\u662fGoogle\u7528\u6237\u771f\u5b9e\u66dd\u5149\u4efd\u989d"
  },
  {
    "id": "GO-MET-003",
    "version": "1",
    "definition": "weighted_visibility=\u03a3\u56fa\u5b9aquery\u6743\u91cd\u00d7\u83b7\u51c6CTR\u6a21\u578b(position)/\u03a3\u6743\u91cd",
    "limitations": "\u4ec5\u540ccohort/market/device/surface\uff1b\u9ed8\u8ba4\u6743\u91cd1\u6216\u663e\u5f0f\u91cf\u6743\u91cd\uff1b\u6a21\u578b\u4f30\u8ba1\u4e0d\u662f\u5b9e\u9645\u8bbf\u95ee"
  },
  {
    "id": "GO-MET-004",
    "version": "1",
    "definition": "feature_presence/ownership_rate=\u6709feature\u6216\u76ee\u6807\u62e5\u6709feature\u7684\u6709\u6548run\u6570/\u5177\u68c0\u6d4b\u80fd\u529b\u7684\u6709\u6548run\u6570",
    "limitations": "\u6ca1\u6709\u68c0\u6d4b\u80fd\u529b\u4e0d\u7b97absence\uff1bAIO\u662f\u5426\u51fa\u73b0\u4e0e\u662f\u5426\u5f15\u7528\u76ee\u6807\u5206\u5f00"
  },
  {
    "id": "GO-MET-005",
    "version": "1",
    "definition": "rank_change=\u53ef\u6bd4\u524d\u671f\u4f4d\u7f6e\u2212\u540e\u671f\u4f4d\u7f6e\uff0c\u6b63\u4e3a\u6539\u5584",
    "limitations": "\u53ea\u5bf9\u4e24\u8fb9\u90fd\u89c1\u5230/\u540c\u6e90\u540cscope\u7ed3\u679c\uff1b\u6389\u51fa\u6df1\u5ea6\u53e6\u62a5censored transition\uff0c\u4e0d\u865a\u6784\u6539\u53d8\u91cf"
  },
  {
    "id": "GO-MET-006",
    "version": "1",
    "definition": "volatility=\u56fa\u5b9acohort\u4e2d\u9884\u5b9a\u4e49\u53d8\u5316/\u8fdb\u51fa\u6df1\u5ea6/feature\u8f6c\u79fb\u7387",
    "limitations": "\u9608\u503c\u3001\u7f3a\u6d4b\u3001source\u5207\u6362\u5206\u5f00\uff1b\u4e0d\u662fGoogle\u7b97\u6cd5\u66f4\u65b0\u786e\u5b9a\u5f52\u56e0"
  },
  {
    "id": "GO-MET-007",
    "version": "1",
    "definition": "search_demand=\u6e90\u5b9a\u4e49\u7684\u6708\u91cf/\u5706\u6574\u91cf/\u4f30\u7b97\u91cf\uff1bclose variants\u7ec4\u552f\u4e00\u8ba1",
    "limitations": "\u4e0d\u80fd\u628a\u91cd\u590d\u7ec4\u91cf\u5206\u914d\u6bcf\u8bcd\u540e\u518d\u603b\u548c\uff1bTrends index\u4e0d\u4e0esearches\u76f8\u52a0"
  },
  {
    "id": "GO-MET-008",
    "version": "1",
    "definition": "trend_change=(\u672c\u671f\u540c\u5c3a\u5ea6\u503c\u2212\u57fa\u671f\u503c)/\u57fa\u671f\u503c\uff1b\u57fa\u671f>0\u4e14\u53ef\u6bd4",
    "limitations": "\u4f4e\u91cf0/\u7f3a\u5931\u4e0d\u7b97\u65e0\u9650\u589e\u957f\uff1b\u540c\u6bd4\u8981\u540c\u5b63\u8282\u7c92\u5ea6\uff0c\u7a97\u7f29\u653e\u53d8\u5316\u53e6\u6821\u51c6"
  },
  {
    "id": "GO-MET-009",
    "version": "1",
    "definition": "brand_demand_share=\u56fa\u5b9a\u54c1\u724c\u8bcd\u7ec4\u53ef\u53bb\u91cd\u9700\u6c42/\u56fa\u5b9a\u54c1\u724c\u8bcd\u5b87\u5b99\u9700\u6c42",
    "limitations": "keyword overlap/close variants\u4ea4\u53c9\u53ef\u63a7\uff1b\u5426\u5219\u53ea\u62a5\u544a\u76f8\u5bf9\u8bcd\u96c6\u4efd\u989d\uff0c\u4e0d\u53eb\u5e02\u573a\u4efd\u989d"
  },
  {
    "id": "GO-MET-010",
    "version": "1",
    "definition": "news_voice=count distinct eligible publisher article\uff1boccurrences\u53e6\u8ba1",
    "limitations": "\u8f6c\u8f7d/\u8fd1\u91cd\u590d/\u4e8b\u4ef6\u7c07\u5206\u5f00\uff1b\u65e0\u5168\u7f51\u5206\u6bcd\u4e0d\u79f0\u5168\u5a92\u4f53\u5360\u6709\u7387"
  },
  {
    "id": "GO-MET-011",
    "version": "1",
    "definition": "AI_mention_rate=\u81f3\u5c111\u6b21\u663e\u5f0f\u54c1\u724cmention\u7684\u6709\u6548\u56de\u7b54\u6570/\u5df2\u89e6\u53d1\u4e14\u5b8c\u6574\u53ef\u5224\u5b9amention\u7684AI\u56de\u7b54\u6570",
    "limitations": "AIO\u672a\u89e6\u53d1\u4e0d\u5165\u8be5\u6761\u4ef6\u5206\u6bcd\uff1b\u540c\u65f6\u8fd4\u56defeature\u89e6\u53d1\u7387\u548c\u8ba1\u5212\u8986\u76d6\uff1b0\u6709\u6548\u56de\u7b54\u8fd4\u56denull\uff0bno_eligible_answers\uff0c\u4e0d\u4ee3\u8868\u771f\u5b9e\u7528\u6237\u66dd\u5149"
  },
  {
    "id": "GO-MET-012",
    "version": "1",
    "definition": "AI_citation_rate=\u5305\u542b\u76ee\u6807\u57df/\u9875\u53ef\u89c1\u5f15\u7528\u7684\u6709\u6548\u56de\u7b54\u6570/\u6e90\u5177\u5f15\u7528\u80fd\u529b\u4e14\u5f15\u7528\u7ed3\u679c\u5b8c\u6574\u53ef\u5224\u5b9a\u7684AI\u56de\u7b54\u6570",
    "limitations": "\u4e0emention\u53ef\u6709\u4e0d\u540c\u6709\u6548\u5206\u6bcd\uff0c\u5206\u522b\u8fd4\u56de\uff1b\u6210\u529f\u5b8c\u6574\u4e14\u65e0\u5f15\u7528\u4e3a\u771f0\uff1b\u6e90\u672a\u63d0\u4f9b/\u672a\u5c55\u5f00\u6392\u9664\uff0c0\u6709\u6548\u56de\u7b54\u8fd4\u56denull\uff0bno_eligible_answers"
  },
  {
    "id": "GO-MET-013",
    "version": "1",
    "definition": "AI_SoV=\u672c\u54c1\u724c\u6309\u56de\u7b54\u53bb\u91cdmention\u6570/\u540c\u4e00\u6709\u6548\u56de\u7b54\u96c6\u5408\u5185\u56fa\u5b9atracked brand\u96c6\u5408\u6309\u56de\u7b54\u53bb\u91cdmention\u603b\u6570",
    "limitations": "\u591a\u54c1\u724c\u56de\u7b54\u53ef\u8d21\u732e\u591a\u4e2amention\uff1b\u5206\u6bcd\u4e3a0\u8fd4\u56denull\uff0bno_tracked_mentions\uff1b\u96c6\u5408\u7248\u672c\u53d8\u66f4\u4ea7\u751f\u65ad\u70b9\uff0c\u4e0d\u548cmention rate\u6df7\u7528"
  },
  {
    "id": "GO-MET-014",
    "version": "1",
    "definition": "AI_first_mention_position=\u56de\u7b54\u4e2d\u5168\u90e8\u5df2\u8bc6\u522b\u4e14\u6d88\u6b67\u54c1\u724c\uff08\u542b\u672a\u8ddf\u8e2a\u54c1\u724c\uff09\u6309\u9996\u6b21\u51fa\u73b0\u53bb\u91cd\u6392\u5e8f\uff0c\u53d6\u76ee\u6807\u54c1\u724c\u6761\u4ef6\u5747\u503c/\u4e2d\u4f4d",
    "limitations": "\u4fdd\u7559\u54c1\u724c\u8bc6\u522b\u7248\u672c\uff1b\u4ec5\u5728\u63d0\u5230\u76ee\u6807\u7684\u56de\u7b54\u4e2d\u805a\u5408\u5e76\u540c\u65f6\u8fd4\u56demention rate\uff1b\u4e0d\u51fa\u73b0\u4e0d\u586b\u672b\u4f4d\uff0c\u65e0\u5408\u683c\u6837\u672c\u4e3anull\uff0c\u4e0d\u79f0\u81ea\u7136\u6392\u540d"
  },
  {
    "id": "GO-MET-015",
    "version": "1",
    "definition": "GSC_CTR=\u03a3clicks/\u03a3impressions\uff1bposition\u6309\u540cgrain impressions\u52a0\u6743",
    "limitations": "site/page\u3001search type\u3001filtered/anonymous/top_rows\u4e0d\u53ef\u4e71\u6df7\uff1bquery\u5206\u7ec4\u548c\u4e0d\u7b49\u4e8e\u603b\u91cf"
  },
  {
    "id": "GO-MET-016",
    "version": "1",
    "definition": "GSC_BQ_position=site SUM(sum_top_position)/SUM(impressions)+1\uff1bURL\u7528sum_position",
    "limitations": "BQ\u4f4d\u7f6e0\u57fa\uff0cAPI\u5747\u503c1\u57fa\uff1b\u4e0d\u80fd\u5e73\u5747\u5df2\u5e73\u5747position\uff1b\u8868grain\u5355\u72ec"
  },
  {
    "id": "GO-MET-017",
    "version": "1",
    "definition": "qualified_opportunity=\u900f\u660e\u52a0\u6743\u7684\u53ef\u89c1\u6027gap\u3001\u53ef\u6bd4\u9700\u6c42\u3001\u4e1a\u52a1\u76f8\u5173\u6027\u3001\u6267\u884c\u6210\u672c\u4e0e\u8bc1\u636econfidence",
    "limitations": "\u8bc4\u5206\u548c\u6743\u91cd\u516c\u5f00\uff1b\u7f3a\u56e0\u5b50\u6807partial\uff0c\u4e0d\u5047\u5b9a\u641c\u7d22\u91cf\u5c31\u662f\u53ef\u5b9e\u73b0\u9500\u91cf"
  },
  {
    "id": "GO-MET-018",
    "version": "1",
    "definition": "local_top3_share=\u6210\u529f\u4e14\u6df1\u5ea6\u22653\u7684\u70b9\u4e2d\u76ee\u6807\u8fdb\u524d3\u70b9\u6570/\u6709\u6548\u70b9\u6570",
    "limitations": "\u5b8c\u6210\u8986\u76d6/\u6392\u9664\u6c34\u57df/\u95f4\u8ddd\u6743\u91cd\u53e6\u5217\uff1b\u70b9\u4e0d\u662f\u5b9e\u9645\u7528\u6237\uff0c\u975e\u5382\u5546SoLV\u540c\u53e3\u5f84\u9664\u975e\u660e\u786e\u6620\u5c04"
  },
  {
    "id": "GO-MET-019",
    "version": "1",
    "definition": "review_target_sentiment=\u72ec\u7acb\u83b7\u51c6\u6837\u672c\u4e2d\u5bf9\u76ee\u6807\u53ef\u5224\u5b9a\u60c5\u611f\u7684\u5206\u7c7b\u6bd4\u4f8b",
    "limitations": "\u6392\u5e8f/\u622a\u65ad/\u672a\u77e5/\u8bed\u8a00\u5355\u5217\uff1b\u4e0d\u5f97\u7528\u5b98\u65b95\u6761\u76f8\u5173\u8bc4\u8bba\u4f30\u8ba1\u5168\u90e8\u6d88\u8d39\u8005\u6001\u5ea6"
  },
  {
    "id": "GO-MET-020",
    "version": "1",
    "definition": "GA4 key_event_rate/users/sessions\u7b49\u91c7\u7528\u6e90\u5b9a\u4e49+scope/identity/attribution\u7248\u672c",
    "limitations": "distinct\u901a\u5e38\u975e\u53ef\u52a0\uff0cHLL++/threshold/modeling\u53ef\u80fd\u5b58\u5728\uff1b\u4e0d\u80fd\u628aBQ\u91cd\u7b97\u503c\u5f3a\u88c5\u6210Data API\u503c"
  },
  {
    "id": "GO-MET-021",
    "version": "1",
    "definition": "Ads CTR/CPC/CPM/CPA/ROAS\u6309\u517c\u5bb9\u7a97\u53e3/\u5e01\u79cd/\u5f52\u56e0\u5b9a\u4e49\u7b97\uff1bcost_micros\u00f71e6",
    "limitations": "conversions\u53ef\u5c0f\u6570\uff1bROAS\u4e0d\u662f\u5229\u6da6\u6216\u56e0\u679c\u589e\u91cf\uff1b\u7ade\u54c1spend estimate\u53e6\u6307\u6807"
  },
  {
    "id": "GO-MET-022",
    "version": "1",
    "definition": "search_to_business_reconciliation=\u6309\u83b7\u51c6\u7c92\u5ea6\u5bf9GSC clicks\u3001GA4 sessions/key events\u3001Ads clicks\u7b49\u4f5c\u5dee\u5f02\u5206\u89e3",
    "limitations": "\u4e0d\u5047\u8bbe\u4e00click\u4e00session\uff0c\u4e0d\u505a\u672a\u6388\u6743\u7528\u6237\u7ea7\u62fc\u63a5\uff1b\u53d8\u5316\u53ea\u80fd\u5173\u8054\uff0c\u4e0d\u81ea\u52a8\u56e0\u679c\u5f52\u56e0"
  }
]
