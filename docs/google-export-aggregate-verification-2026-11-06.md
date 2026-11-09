# 导出清单汇总校验补齐（2026-11-06）

关联 GO-API-108、GO-AC-007、GO-ING-003。此修复仅在本地；不代表正式平台准入、真实百万行或七天验收。

## 已确认缺口

原 `verify_bundle` 校验文件hash、JSONL/请求CSV一致性、逐occurrence内容/顺序，但仅复算请求和occurrence总数。若汇总写错且manifest自身hash重算，唯一URL、重复URL、有效URL、话题、无效项、未解析News wrapper、来源、可用状态、News时间质量错误均可能通过。

本次在逐行核对时复算全部上述汇总，并与manifest比较。唯一URL使用独立临时SQLite索引，避免随URL量增长的内存集合；临时目录与输入包分开，退出即清理，输入包保持只读。hash校验仍只证明包内一致性，不授予来源真实性或外部签名可信性。

## 验证证据

`state/google-data-closure-20261106/export-aggregate-audit/`：

- `prior-red.log`：使用既有冻结Web导出校验器执行新负例，9种错误统计均未拒绝（9个断言失败，预期RED）。
- `targeted-green.log`：新校验器、隔离PostgreSQL真实导出/恢复/截断/私有隔离及DTO共16项通过，0跳过。
- `targeted.log`：初次本地数据库端口未显式指定导致连接错误，保留原记录；随后显式绑定任务端口55436并通过，未改断言。
- `million.log`：已有10,000请求/1,000,000 occurrence合成包校验通过，51.18秒、进程峰值RSS约40.4MiB；合成数据不替代真实平台容量。

本轮没有真实搜索、Feed调用、平台读写或生产发布；本任务PostgreSQL已停止。生产冻结版本仍为原校验器，下一步须按两端差异准备最小候选、冻结验证后再发布，不能将本地修复描述为上线。

## 后续状态

上述为本地阶段时点记录。后续已完成单文件Web发布、23项冻结测试及三来源旧请求/API核对，见 `google-export-aggregate-release-2026-11-06.md`；未发布到不存在该模块的采集机。
