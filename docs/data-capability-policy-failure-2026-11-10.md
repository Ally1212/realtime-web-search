# 执行后准入失败原因（本地第三十阶段）

执行器保留已知的来源/原文许可、准入变化与到期错误码，不再把这些错误统一记录成 source_outcome_unresolved。只允许固定代码列表，异常消息和任意适配器代码不写入任务或 dispatch；其他失败仍使用通用代码。

费用行为保持保守：来源返回后因撤权无法保存事实，仍进入 reconciliation_required，保留已知 actual_cost 证据和额度预留，不自动退款、不伪报成功、不自动重试。人工对账确认才结算。仅重新授权不会重放调用。

四域合成来源新增用例覆盖调用后撤销原文许可、零事实保存、任务和 dispatch 原因一致、费用证据保留、重新授权后零重放。专项 204 passed / 262 subtests；全量结果与清理记录以 state/data-capabilities-20261110/policy-reason-manifest.json 为准。本轮未部署、未调用真实来源或平台；真实来源、正式平台和七天验收仍未完成。

最终全量隔离回归 1445 passed / 821 subtests / 0 skipped / 0 failed（385.85秒）；专项204项/262子测试（33.64秒，与全量重叠）。自建 PG/Redis 停止、端口关闭，合成 PG 与空 socket 目录清理，日志保留。web.py / serp_page.py 摘要不变。
