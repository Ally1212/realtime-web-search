# 原文当前权限检查（本地第二十九阶段）

事实完成保存、完整观测读取及订阅首次生成候选时，若 raw.payload 非 null，当前来源必须显式 raw_response_retention_allowed=true；缺失或撤回均拒绝，包含脱敏后仍保留的原文。仅保存结构化事实、raw.status=not_retained 的结果不要求原文许可。不改写既有快照、摘要或历史账目，也不声称已物理清除旧原文。

隔离 PostgreSQL 专项：22 passed / 19 subtests，包含四域保存前缺失权限拒绝、保存后撤权读取拒绝、重新授予后原快照一致，以及四域仅结构化事实可保存读取。日志：state/data-capabilities-20261110/raw-rights-initial/focused.log。本轮自建 PG/Redis 已停止，合成 PG 目录及空 socket 已清理。

已扩大验证：完整任务导出、缓存复用、投影重建、订阅待确认批次重交付与确认均在原文许可撤回后拒绝；重新授权后原快照及批次不变。四域均覆盖。订阅首次生成使用同一份当前 grant 检查来源和原文权限，避免一次操作中二次解析出不同授权。

全量隔离回归 1444 passed / 817 subtests / 0 skipped / 0 failed（211.92秒）；运行期间补充的测试与最终小幅授权绑定调整，又以最终源码执行专项 203 passed / 258 subtests（34.52秒），两轮重叠不能累计。证据分别为 raw-rights-regression/pytest.log 与 raw-rights-final/focused.log；清理和摘要见同目录上级 raw-rights-manifest.json。

这是原文内嵌事实的当前许可门禁，不是整来源清除、平台清除或发送瞬间的严格屏障。独立原响应仓仍沿用自己的 response_retention_not_admitted 错误；事实路径返回 raw_retention_not_admitted。已下载文件无法撤回，纯离线导出 builder 不构成授权入口。尚缺生产装配、真实来源许可/费用/金标、正式平台及规模/七天验收。本轮未部署、未发起真实来源或平台请求，不能据此认定撤权全链路或全部需求完成。
