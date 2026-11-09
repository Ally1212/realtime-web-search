# Feed保存完整性：两机最小冻结候选验证（2026-11-06）

本轮没有部署、采集、平台请求、迁移或开关变更。已把上一轮本地Feed修复叠加到实际两机冻结源码，不能用工作树整文件覆盖它们。

## 候选范围

五个文件副本、三个模块：worker的google_feeds.py/google_data.py；Web的google_feeds.py/google_data.py/google_legacy.py。包含无效条目证据/源位置、新版本标记、全无效News的parse_error以及实际失败分类筛选。Web与worker共同保留新Feed标记语义；没有新增API、UI、分析、数据域或原始字节留存。

Web的Feeds/Store与worker确有既有差异。首次自动套用Worker diff在Web的news_time import上下文失败，已保留reject/partial记录，不用fuzz放宽匹配；改为只替换共同的精确import，再应用共同业务补丁。Web的publication/采集时间写入等旧差异原样保留，不夹带worker历史改动。

正常化模块只插入三个新Feed分支；移除这三段后与各机before源码逐字节一致，证明Images分支、旧记录身份和其他逻辑没有顺带改变。Feeds中article、date/text、traffic、rows、collect_sources等未修改函数AST全部一致。保护证据scope-protection.json。

实际两台生产机五个目标文件before hash均再次只读核对一致，未修改文件。源端点只读SSH文件hash不是上游数据验收，不据此假定平台可读。

包 `state/google-data-closure-20261106/feed-integrity/frozen/feed-candidate.tar`，SHA256 `f83913ea7649004233cd07afa5ad0300c421b720543412c3408519a6ad359bdc`，只含五个发布目标，不含以下验证支持模块。

## 验证范围和修正过的测试边界

worker48项通过，Web48项通过，分别0失败/跳过/包外realtime模块/非loopback连接。两组高度重叠，不相加冒充96项独立需求验收。所有操作在本机独立PostgreSQL schema及mock Feed HTTP中，不是真实RSS/Whale或生产故障注入。

worker原包没有Web旧数据读取/导出模块，首轮守卫发现借用工作树google_delivery。最终明确从Web候选绑定google_legacy/google_data_export/google_delivery为验证支持，不作为worker发布文件。

Web旧SerpStore不写worker才有的collection_times，首轮继承的生产写时间用例因此失败。没有改变Web的SerpStore来迎合测试：仅该用例使用实际冻结WorkerStore的验证别名写入共享schema，再经实际Web读取函数验证requested_at/received_at/未知source_observed_at；原时间幂等断言仍完整执行。其他用例继续原Web fixture。

初次把全部Web写入fixture换成worker别名，导致旧tracking测试的模块patch不再作用而失败；因此将producer角色限定到唯一跨机时间用例，保留其他原fixture。新增时间读取验证初次误以为recent返回request_id，已改为只读查询真实主键。初次业务失败与绑定失败日志/JSON均保留，没有删除失败测试。

覆盖包括全无效Feed保存/失败状态、有效/无效源位置、重复报道、原有URL去重/历史时间、私有隔离、API底层读取函数与同条件JSONL/CSV/清单验证。没有直接证明正式HTTPS读取新记录，因为本轮没有真实新Feed采集，也未发布。

## 收尾与后续

全部任务测试schema0残留，专用PostgreSQL55436已停止，未启动本机Docker。原 v2 Web 单次候选未被本轮改写或解禁。

下一步如发布须再次校验两机before/protected hash、四开关false与在途0，逐机精确备份/原子替换，仅重启Web/prd/local-worker，再核对全部业务不变性。不能使用有其他历史变更的工作树整包或旧Compose，也不能为验收启用News/Trends。

仍未完成完整RSS原字节留存、正式许可/平台合同/ReaderAdmission、逐源真实终态/固定版本、七天/真实百万行。Consumer最近402 credit_insufficient与真实运行授权条件未变（本轮未再次调用平台）。这只是局部候选验证，GO-SRC-007、GO-DAT-002/010、GO-ID-002/006、GO-ING-001、GO-POC-05整体仍未验收。

证据目录 `state/google-data-closure-20261106/feed-integrity/frozen/`：verification-summary、两个tests/log、各阶段初次失败、candidate-manifest、validation-support、scope-protection、production-before-hashes和五文件tar。

后续五文件冻结候选已上线，先Web后worker，只重启web/prd/local-worker；源请求/旧队列/四开关保持不变。原生模拟及HTTPS5项通过，未真实采集或平台调用。详见google-feed-integrity-release-2026-11-06.md。
