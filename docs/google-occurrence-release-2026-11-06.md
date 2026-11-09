# 同响应occurrence修复上线（2026-11-06）

已将实际冻结worker的两个最小文件free_google.py、serp_fields.py发布，仅重启local-worker。不是工作树整文件覆盖：发布内容来自实际冻结源上的精确补丁，hash与此前116项通过的候选一致。不包含原始字节加密模块/新密钥/新依赖安装或任何配置开关变更。

## 实际行为

同一OpenSERP响应的重复URL保留独立有序organic_results，供SerpStore保存/规范化为不同occurrence；下游SearchResult工作列表仍按URL去重，URL registry保持一份唯一链接。成功envelope增加明确标记的过滤上游JSON（public_fields），保留结果顺序/重复项/未知公开字段，不冒充原始HTTP字节。历史已丢失的项不能补回。

包SHA256 `77a84bdf8af50bed0300920f07e952f0ed9d7cbed4fbf3ba26a870eb14f3ffd0`。

| 文件 | before | after |
|---|---|---|
| free_google.py | 23458c8d3b905ec3882b3bcaf7d4ae03fbab28603464965fa5d02156d19138ef | 27f84522977ec6e756c4dc3979a0a712098c084ffc12de9978aeed7f11f08bac |
| serp_fields.py | 7ae4e4a4fc04f4099f12d451ecf833fb5f4d3c2cd17996191d8277631fe9ad6c | 9fbb72ca81f7c8558829466f9f6d00a3b8b19c61bcfc3d6de67373daa38abf69 |

## 发布验证与限制

发布前四开关关闭、任务/lease/Whale/customer在途/监测0，目标before hash与全部保护源码相符。独立备份完成后逐文件CAS校验/原子替换，启动前再次只读核对数据。发布后目标after hash相符，其他采集机四容器与两台Web容器ID/启动时间/镜像/排序后的挂载不变；web.py、serp_page.py、google_data.py、Google Images及其他保护源码未覆盖。

生产容器中的进程内模拟HTTP验证：相同URL两条结果得到1条下游工作项、2条有序保存结果，upstream_response两项均保留，凭据键过滤；没有真实Google/平台HTTP、数据库写入或结果页访问。raw_response_recorder未配置，google_raw_capture模块仍不存在。worker running、OOM false、restart_count0、新启动日志Traceback0。

三份业务快照一致：请求39840（Web31679/News8127/Trends29/Images5）、pages332768、旧outbox9queued、continuous/content_fetch/uploads/claims均false、手动任务与分页/查询/上传lease、客户在途任务、启用监测均0。原registry0，新spool/ledger/domain exports表仍未创建。没有重传、迁移或历史修补。

此前116项冻结依赖测试作为候选回归证据；本次新增的是hash/服务/业务不变性与生产进程内模拟验证。没有把127项raw加密候选测试当作本次发布内容，也不宣称真实新采集/平台交付验收。

## 精确回退

采集机项目state/occurrence-release-20261106-0910含两个原文件（600）、journal.json、manifest.json、通用release-functions.py及绑定本包SHA的rollback.py。提供90秒内完整只读数据库前检JSON后运行rollback.py，校验所有当前old/new hash、保护文件与全部备份后恢复。然后仅重启local-worker并重复业务/服务检查。不能直接运行旧四文件bundle入口；本helper明确覆盖本次两文件bundle身份。不回滚数据库、不恢复旧Compose、不改其他服务。

生产未执行回退；通用文件故障/部分替换回退路径此前在本地验证，本轮helper已编译并保存在备份目录，hash `570cb508608539820c2fb135c9fd2458743906a7aafc95ce58f1753a4ef47c18`。

## 剩余条件

正式来源/用途与ReaderAdmission、原字节私有留存启用/密钥/备份、Consumer额度恢复（最近402 credit_insufficient）、旧摘要异常、新运行明确授权、News/Trends真实交付及七天/真实百万行均未完成。旧v1 sidecar仍有旧transport代码且暂停，不能因为常驻worker已修复而重新启用旧v1。

证据目录 `state/google-data-closure-20261106/occurrence-fix/release/`：manifest/installed/restart、前后inventory/数据库、runtime-fixture-smoke、runtime-health、final-business-check、rollback-helper-proof及verification-summary。对应GO-DAT-002、GO-ID-002、GO-DAT-010、GO-ING-001只增加本次局部上线证据，不把整项升为已验收。
