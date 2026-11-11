# 中台已有真实数据：首次读取与导出贯通

用户明确改为使用中台已有真实数据，不要求重新购买来源或提供本地文件。本轮使用现有Consumer Key，经ego-browser在已登录Marsyoo组织中只读核验；不显示密钥、不换Key、不改额度或权限、不新采集或上传。

## 实际结果

- 文档GET、固定版本batch-get、近30天授权目录browse及网站同一请求接口均HTTP 200。控制台显示余额0，但这次读取成功；不能据余额推断当前读接口阻塞，也不能推断所有操作免费。
- 文档doc_9fb5017c5add01bc17809c40dade7d60，固定版本ver_4a29e4654356820e56abd6407398d5b0；request 05677d68-57e4-4cd0-9e7c-cbeb585a016b，关键词love，7条Google Web结果。固定载荷schema为google-serp-run-envelope-1。
- 平台raw_payload_hash与完整载荷canonical摘要一致，observation.source_response_hash与源响应摘要一致。中台source_response与正式网站/api/serp/requests/{id}返回的完整对象相等，不只是7条数量相同。
- 当前Consumer在social_media_raw、google_search/google_news/google_trends过滤、近30天、limit20范围返回10个文档，无next_cursor，均为google_search。仅表示该授权和窗口可见范围，不能证明中台全量或其他域不存在。

## 可复用实现与真实产物

新增realtime/google_middle_export.py：从Consumer读回的固定Web运行载荷生成完整platform-payload.json、observation.json、source-response.json、readback-proof.json、occurrences.jsonl、occurrences.csv与校验manifest，打包ZIP。CSV保留完整record_json，JSONL逐出现保留，同URL不合并。要求原响应、完整载荷、身份及摘要一致，限额拒绝不截断；纯builder验证一致性，不授予来源、租户或平台权限。

read_middle_web_bundle(client,target)复用现有授权Consumer读回方法，不查询旧数据库、不发起搜索或上传。在线应用仍必须提供可信Consumer、允许的Dataset/source和租户权限，不能把共享Key交给浏览器。此模块尚未挂载到生产HTTP路由。

真实产物位于state/middle-platform-real-integration-20261110/，目录700、文件600；middle-platform-real-sample.zip包含7条真实结果及完整载荷。逐项验证CSV record_json、JSONL和ZIP内容与固定版本相等。verification.json记录真实HTTP状态、范围、摘要与对账结果；不含密钥。

相关隔离测试16项/26子测试通过，覆盖重复出现不合并、JSONL/CSV/ZIP完整、确定性打包、原文/摘要/身份/来源篡改拒绝。没有把这些测试计作新的四域真实金标或全量回归。测试命令：`.venv/bin/python -m pytest -q tests/test_google_middle_export.py tests/test_google_whale_readback.py tests/test_google_serp_delivery.py`。

## 尚未完成

这证明一条已有Web记录从中台读取、完整导出、与正式网站数据对齐；不证明正式网站已切换成中台直读，也不证明全量同步。源码customer_data.py的现有客户数据入口仍标记saved_serp_requests_not_whale_readback，不能仅因内容相同就推断运行时数据源相同。

历史数值热度、关键词搜索量、完整AIO/AI Mode是否已在中台并可由当前应用授权读取，尚未确认；本样本不能替代这四域验收。下一步应核对相应目录/字段及读取权限，并准备明确区分来源的生产中台读路由；不把现有Web结果伪装成四域数据。

浏览器独立Space123已finish且列表核验不存在；用户其他空间不动。本轮无生产代码发布、数据库迁移、采集开关修改、上传、充值或Git提交。
