# AI采集数据Whale上传小样测试（2026-11-11）

用户授权测试上传，实际仅本AI任务Web/News各一条，共2条，不执行全量1385链接同步。经正式网站管理API创建两个限量上传任务，短时启用uploads，claims始终false；没有其他active/paused上传任务才开始。网页真实创建、worker提交各一次，随后短时等待处理回执，所有测试结束恢复uploads=false/claims=false并暂停这两个测试任务，保留证据。

## 结果与边界

- Web job `3d051465-cc04-4c8e-86fb-7f301bc52192`；News job `5af9cb4b-8a07-4c0c-a723-a1e07f4d5f3f`。两条attempts=1、均queued、0accepted/duplicate/rejected，具有document/version ID；独立处理回执也为queued，未返回persisted_at。提交回执outbox_event_created=false；其原因本轮未知，不推断为成功持久化。
- Consumer独立凭据读回：初轮Web文档404/版本envelope200但item not_found，News遇到429。再次每请求间隔2秒复核，两条文档GET均404，固定版本batch-get均HTTP200但单项not_found；绝不能把HTTP200信封当作读回成功。已接收2/2，最终持久化0/2可核验，独立正文读回0/2。
- 因此仅可确认网站到Whale提交接口可达并接收，不能称上传全流程正常。平台异步处理尚无终态，原因不明；限流复核后仍不存在，不将其归咎于单次429。未更换Key/充值/改平台权限或平台服务。
- 最终本地总outbox由旧9增为11条queued，2测试任务paused，上传与领取开关false；AI采集任务仍按时停止，未重启采集或新发搜索。暂停本地任务不能撤回平台已接收请求，平台后续可能继续处理。

证据`state/ai-whale-test-20261111/`，目录700/JSON600：jobs-created、两来源receipts、consumer-readback及consumer-readback-retry、status-final。额外尝试SSH复查时，首次只读查询因receipts投影缺dataset_id在发出请求前失败；补齐本地投影后的再次SSH被连接关闭，没有获得更新的Writer回执，不覆盖前面已保存的queued证据。独立Consumer读取凭据仅经stdin传递给采集机SDK，不输出/保存密钥。

浏览器ego143已finish无保留页面；没有部署代码、旧数据库迁移或新增后台长期任务。后续需检查平台接收后的持久化链路，终态和读回一致后再考虑全量上传；本轮没有执行该后续操作。

## 再次核验（约新加坡09:55）

经采集机配置的真实Writer endpoint `https://www.marsyoo.com`复核：`POST /v1/ingest/receipts`两条仍queued，submitted_at均UTC01:46:03，updated_at仍同秒，无持久化时间；`GET /v1/documents/{id}`两条404；`POST /v1/versions/batch-get`HTTP200但各单项not_found。相同Consumer凭据、同一endpoint查询已知历史文档doc_9fb5017c5add01bc17809c40dade7d60返回200，证明接口及凭据并非整体失效，但不据此推断任何新文档均有读取权。

本次只读，无重新提交、重试上传或改变开关。证据consumer-readback-recheck.json（600），包含平台原回执及独立读回。没有读平台内部worker/日志，无法确定排队或处理故障的具体原因。经应用机跳板的SSH先被关闭，改用已授权采集机直连成功，未绕过主机校验或改变网络配置。
