# 导出汇总校验最小发布（2026-11-06）

关联 GO-API-108、GO-AC-007、GO-ING-003，仅新增局部上线证据，完整需求仍未验收。

## 实际发布

实际盘点确认：`google_data_export.py` 仅在Web服务器生产包存在，采集机不存在此模块。因此本次只替换Web的一个文件，没有部署到采集机。只更新 `verify_bundle` 并添加逐行汇总辅助函数；其他生产函数AST一致，替换区间之外的原文件字节保留。候选包SHA256 `122cb52fc5d8e5ace25e17ed564694e3feb46f4262303739d4dd816c151b4ec5`，文件发布后SHA256 `8c455d80f8ff350500842d964a8c7a1b947cb32f4a35adeb51928a49d151d00f`。

该模块仅独立CLI引用，未被常驻Web服务导入，因此没有重启任何容器。发布工具验证其他生产包Python文件hash、两机容器ID/启动时间/挂载和保护文件不变。没有数据库迁移、真实搜索、Feed请求、平台读写或开关变更。

## 验证与边界

- 从生产实际包构造冻结候选；23项隔离PostgreSQL测试通过，0失败/跳过，运行模块全部来自候选，外网连接尝试0。初次冻结测试缺少独立测试辅助脚本，保留失败记录；补入原辅助脚本后通过，未修改断言，该脚本不在发布包。
- 线上仅导出已有公开Web 1请求7结果、News 1请求86结果、Trends 1请求10话题30报道。三格式校验通过，逐请求标准化内容与保存源记录相同。每个包故意写错唯一URL汇总并重算manifest hash，新校验器均拒绝；恢复后再次通过。临时文件全部删除。
- HTTPS未认证401、认证health200；三来源同一request详情各200，规范化API内容hash与上述保存源记录一致。
- 只读快照：请求39,840、pages332,768、旧outbox9queued；持续采集/正文/上传/领取false，活动手动任务、有效lease、客户运行任务及启用监测0。发布及真实导出前后这些状态一致。正式registry仍0，新spool/ledger/正式导出表仍不存在。

News 86条全部仍是未解析包装链接；Trends是Trending Now话题/报道，不是数值历史曲线。这只是每来源一个旧请求的导出/API一致性证据，不代表独立新采集、平台终态/Consumer、正式ReaderAdmission、真实百万行或连续七天验收。

## 证据及回退

本地 `state/google-data-closure-20261106/export-aggregate-release/` 保存实际盘点、候选、23项冻结测试、安装前后hash/服务/数据、真实导出及HTTPS检查。原缺口与合成百万行验证见 `google-export-aggregate-verification-2026-11-06.md`。

Web服务器备份：`/home/azureuser/projects/google-workbench/state/export-aggregate-release-20261106/`，含原文件、manifest、journal及 `rollback.py`。使用同样字段的新鲜只读数据库快照运行该脚本；只有目标仍为本次before/after hash、全部保护文件未变时才恢复原文件。回退不改数据库，也不恢复其他文件/Compose；此模块独立CLI无需服务重启。本轮未触发生产回退。

剩余：平台最近观察402且未证明恢复；新真实搜索/上传仍待明确授权，来源/用途/正式schema与ReaderAdmission条件未齐。下一步按核心闭环清单处理其余可隔离验证缺口；不得把此次发布当作恢复采集或上传许可。
