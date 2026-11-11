# 指定任务的单样本执行入口

`realtime/data_capability_sample.py` 提供可信 Python 入口 `run_sample_step`。解决普通执行器会先消费其他排队任务的问题：`CapabilityJobs.claim` 与 `CapabilityExecutor.run_once` 新增成对的 target_tenant_id / target_job_id 参数，SQL精确限制租户及任务；不命中、不排队、已完成均不回退领取其他任务。普通后台循环不传参数，仍保持原语义。没有新增客户领取权限或公开路由。

调用方需显式提供已初始化、未运行线程的执行器、当前来源审批、既有预算，以及适用的恢复门禁。入口不创建任务、不修改预算、不初始化数据库、不启线程、不采购、不自动重试。共享账号并发、间隔、费用未对账隔离、lease、当前许可、响应仓与原生参数校验仍走原执行路径。共享scope的既有过期执行维护仍可能更新其他任务元数据，不能称数据库完全隔离；只保证不会改为调用其他排队任务的来源。

```python
from realtime.data_capability_sample import run_sample_step

# approved_executor 为可信启动代码构造的已初始化、未启动线程的执行器。
# queued_job_id 为已有获准任务；此函数不授予调用或部署权限。
sample = run_sample_step(approved_executor, approved_tenant, queued_job_id,
                        maximum_cost=approved_total_ceiling, currency=approved_currency)
```

先检查任务归属、来源及币种/预留费用不超过本轮上界，再进行一个同步步骤。未排队任务明确拒绝，调用者重跑不能制造第二次源请求。节流/未准入仍可能没有实际调用，step_entered不冒充source_calls；实际调用证据留在原执行账本。实际调用仍受适配器自身deadline和传输限制，入口不新增严格总超时承诺。

只有succeeded/partial返回经过当前授权与固定摘要检查的完整ZIP及proof；失败、待对账或仍排队返回bundle/proof=null。partial仍明确partial，不冒充完整。导出未通过检查直接报错，不返回半包。调用后的任务可能已经保存/结算，导出异常不能据此自动重发来源。结果只在内存返回，不自动写文件或上传；内含许可内数据，调用方负责私有保存与外部清除责任。

验证为真实隔离PostgreSQL、合成来源。四域覆盖错租户/不存在任务零调用、跳过更早排队任务、重复执行零调用、币种/费用上界错误拒绝、任务完成及完整ZIP独立校验。全量结果与资源清理见 state/data-capabilities-20261110/sample-step-manifest.json。

这补齐单样本的可执行Python步骤，不是生产配置CLI、HTTP双层鉴权验收或完整启动装配。真实来源账号/许可/费用/预算、恢复检查点独立保管、正式serve接入、Whale准入和规模/七天仍待完成。没有真实来源/平台请求或生产部署。

最终隔离回归1448项/837子测试通过、0跳过/失败（254.31秒）；专项207项/278子测试（40.08秒，与全量重叠）。自建PG/Redis停止、端口关闭，自己的合成PG及空socket目录清理，日志保留。保护文件web.py/serp_page.py摘要不变。

## 私有文件与命令行

新增 `run_sample_file` 及模块命令。运行位置须在项目根目录，使用现有虚拟环境：

```sh
.venv/bin/python -m realtime.data_capability_sample --help
.venv/bin/python -m realtime.data_capability_sample \
  --factory approved_local_module:build_executor \
  --tenant APPROVED_TENANT --job APPROVED_JOB_UUID \
  --maximum-cost APPROVED_CEILING --currency USD \
  --output /approved/private-directory/sample.zip
```

`approved_local_module:build_executor` 是操作者另行编写和审查的可信本地Python代码，不是现成模块，也不能接收客户提供的模块名；它应只返回已初始化且线程停止、具备当前审批与恢复准入的执行器，不自行请求来源/开启线程。工厂导入会执行Python代码，组件不能代替操作者审查。凭据由该可信代码从批准路径读取，不放进命令行。此处不提供真实工厂，不代表已配好账号。

入口拒绝非私有父目录（group/other权限须全部关闭）与已有输出路径；先执行样本，再独立校验ZIP及proof的字节数/摘要，0600临时文件落盘并fsync，发布前重新读取当前授权和固定版本。使用排他hard-link发布，不覆盖竞态中出现的其他文件；finally清理自己创建的临时文件。已下载副本仍无法撤回，最终检查到文件发布之间不构成严格并发撤权屏障。

不输出源内容、凭据或任意异常消息。退出0表示完整文件发布，2表示未得到可交付包，1表示未确认，130表示中断。目录fsync失败或调用后导出失败时任务可能已经收费/完成，须先核对已有任务及文件，不能自动重发来源。受支持文件系统须允许同目录hard-link及fsync；未证明其他文件系统兼容。

本轮新增测试覆盖私有文件、已有文件/竞态保护、撤权/损坏不发布、临时文件清理、CLI异常脱敏，并以隔离PG贯通四域执行账本与真实落盘ZIP校验。结果及清理证据见 state/data-capabilities-20261110/sample-file-manifest.json。本轮只做相关专项回归，不把上一轮1448项全量结果当成本次文件功能的全量验证；真实来源/生产/平台仍未接通。
