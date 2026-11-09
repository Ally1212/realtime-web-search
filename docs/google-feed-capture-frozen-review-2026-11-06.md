# 私有Feed捕获候选冻结审查（2026-11-06）

关联 GO-SRC-001/006/007、GO-DAT-002/010、GO-NFR-007。候选审查通过下述有限范围，**没有部署或启用**；真实来源/用途批准、原实体保存权、密钥与备份生命周期和正式平台验收仍未完成。

## 实际生产基线与最小候选

本轮通过只读SSH取得采集机实际冻结包、Web实际包和共享数据库快照。采集机原包缺少raw capture、journal、rights wrapper，也缺少其依赖的registry/domain/rights模块。因此完整候选必须包含7个文件，而非只补3个留存模块：

- `google_feeds.py`：只替换collect、加入捕获错误类型和所需标准库import；所有其他生产函数AST一致，区间外字节保留。
- 新增 `google_raw_capture.py`、`google_feed_capture_journal.py`、`google_feed_capture_rights.py`。
- 新增必要依赖 `google_registry.py`、`google_domain.py`、`google_rights.py`。保留初始化为显式操作，不能因文件出现而自动运行DDL。此包没有配置、Compose、systemd或安装器，不接入自动调度。

7文件delta包SHA256：`96576de24f089a96ce03d214875bf3fc13b67e8dff835daa379e2a69ae1820c1`。对应manifest列明1个before/after替换、6个原先不存在的新增及43个原生文件的基线hash。候选目录中43个原生文件未改动；今后发布必须重新核验完整基线，不能仅凭本轮快照覆盖后续变更。

默认仍无sink、无vault/key创建、无原字节捕获。只有显式构造并注入批准范围内的rights包装器才进入新增路径。实际registry当前0条，没有来源批准可供执行；文件可导入不是获准运行。

## 冻结验证

`state/google-data-closure-20261106/feed-capture-freeze/`：

- `frozen-tests.json/log`：85项通过、0失败/错误/跳过；运行模块全部绑定候选目录，非loopback连接尝试0。覆盖News/Trending Now、实体、scope隔离、registry前后查权、撤权/期限、提交中断恢复、定向清理及默认关闭。
- 4个当前Web模块仅为验证支持（metrics/legacy/data_export/delivery），原样复制并逐个记录hash；不在7文件运行包内。它们用于测试fixture与公开API/导出比较，不可假称为采集机原生模块。
- 明确不适用的2个测试是OpenSERP私有sink测试，因为此次没有把Web raw sink补丁放入生产候选；名单保留，不以跳过掩饰。此前工作树1002项全量属于另一个源码/环境范围，不能与这85项相加。
- `local-import-smoke.json`：新进程仅导入运行模块，不加载4个测试支持模块、未尝试网络，证明正常运行imports不依赖验证支持。
- `worker-memory-import.json`：在实际local-worker容器中用独立Python进程、`-B`及stdin内存编译七个模块；hash与候选一致，无vault、密钥或代码文件落盘，无registry初始化、来源或平台调用。cryptography50.0.1、psycopg3.3.6、requests2.34.2；AES-GCM内存fixture往返通过。只证明当前镜像可导入/执行所用原语，不代替生产文件系统、密钥治理或长时验收。

初期两机SSH偶发在认证前关闭；`prepare.log`、`prepare-resumed.log`和SSH诊断保留。之后取得两机实际快照、实际容器内存检查及最终只读盘点；未更改SSH安全设置、重启或用旧依赖记录代替新结果。`worker-dependencies.json`为早期失败时点，以后取得的实际版本见 `worker-memory-import.json`，不抹掉失败记录。

## 生产未变化证据

`worker/web-before.json`、`worker/web-after.json`：受盘点文件hash与容器ID/启动时间/挂载相同，所有服务仍running。没有重启容器、安装依赖、修改远端包或写入来源登记。

`database-before/after.json`及`database-no-change.json`：请求39,840（Web31,679/News8,127/Trends29/Images5）、pages332,768、旧outbox9queued；连续采集/正文/上传/领取均false，活动手动任务、有效lease、客户运行任务、启用监测均0，registry0，新spool/账本/正式导出表仍缺失。快照之间这些业务状态一致；这不是持续监控证明。

本地测试schema已清理，专用PostgreSQL已停止；本轮未启动Redis或Docker。只读镜像probe不修改正在运行worker进程的模块，只在独立docker exec Python进程内执行。

## 不能据此放行的事项

- 缺少实际来源整体实体/上下文许可、用途/期限与执行者批准，不能自动创建许可或生产Key。
- 未完成密钥托管/轮换、真实备份/平台副本清除、耐久删除/拒绝重放证据与旧无归属数据处置。当前vault的unlink不是全链路删除。
- 尚无正式安装/回滚流程及生产落盘验收；本包不授权发布。需要按当时完整hash/CAS基线准备精确备份和回滚，并保持默认关闭。
- 新采集及平台写入仍需用户明确授权；平台最近实际观察402，未证明恢复；旧摘要异常、独立News/Trends平台读回、七天、真实百万行与正式ReaderAdmission仍未完成。

后续已完成固定hash安装/回滚工具及独立工具包25项本地验证及实际宿主只读check，见 `google-feed-capture-release-tool-2026-11-06.md`。这补齐了工程准备，不是生产安装/回退演练或来源许可，仍未部署。
