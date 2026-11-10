# 客户数据交付优化与增量订阅

本轮修复排名分析显式 JSON/CSV、接入示例两层认证头、图片字段目录、service_error 状态筛选及国家大小写。保留前置访问门和客户隔离；未开放任意 CORS、浏览器密钥、Webhook 或自动采集。

## 订阅合同

`/v1/data/subscriptions` 是现有客户数据的拉取订阅，与 `/v1/google/subscriptions` 研究域配置不同。

所有调用继续携带 `X-SearchFlow-Access-Key` 和 `Authorization: Bearer <客户Key>`。创建要求 `Idempotency-Key`，创建始终暂停：

```http
POST /v1/data/subscriptions
Content-Type: application/json
Idempotency-Key: my-news-subscription-v1

{"name":"新闻新增链接","mode":"new_urls","filters":{"scope":"public","source":"google_news","query":"energy","country":"US","from":"2026-11-01T00:00:00Z"},"interval_seconds":3600}
```

- filters 支持 scope/source/query/query_match/country/language/classification/from/to/domain/device/page，字符串值；范围默认 own。保存后不可修改，换条件应另建订阅，以免复用旧进度。最多100个订阅/客户。
- `occurrences`：每次结果出现都是事件；相同 URL 在不同请求出现会保留。空结果/错误请求也作为完整请求交付，但没有 occurrence 事件。
- `new_urls`：按本订阅已确认 URL 去重；不是全历史首次发现。批次 `events` 是实际选中项；`requests` 始终保留完整请求，含旧链接、原源字段、话题、富结果和未知值。不要把 requests 的全部 occurrence 重新计作新增。
- `start_mode=history`：从最早匹配已存请求开始；缺少 from 补领全部匹配历史。`start_mode=now`：用创建时的匹配请求快照作为排除基线，之后提交的旧时间戳新请求仍可领取。API 默认 history 保持兼容，网页默认 now；旧订阅保持 history。时间按 UTC，from 包含/to 不包含。
- 网页可以设置历史起始时间（设备本地时区转 UTC），查看精确待处理请求、最近确认、交付记录并重新下载。暂停期间不会丢弃后来入库的请求。

## 拉取、下载和确认

1. `POST /v1/data/subscriptions/{id}/resume`，正文 `{}`。只允许拉取，绝不启动搜索或监测。
2. `POST /v1/data/subscriptions/{id}/pull?limit=20`，正文 `{}`。1–50请求/批，上限8MiB。首次读取保存完整批次；并发重试返回同一批。不接受不稳定的时间游标。
3. 保存 `batch_id`、`payload`、`sha256`。`payload` 摘要按 UTF-8、Python json.dumps(sort_keys=True, ensure_ascii=False, separators=(',', ':')) 计算。文件摘要以校验清单为准，避免跨语言数字序列化差异。
4. `GET .../{id}/batches/{batch_id}/export?format=manifest` 下载数量、文件字节数及SHA256；format=jsonl/csv 下载文件。每个完整请求一行；CSV为五列，含无损 record_json 和 events_json，与原有74列导出用途不同。
5. 文件落盘并校验后，`POST .../{id}/batches/{batch_id}/ack`，正文 `{"sha256":"批次摘要"}`。幂等确认，事务内推进请求/URL账本。响应丢失可重复确认。
6. `POST .../{id}/pause`，正文 `{}`。暂停后仍能重读未确认及历史批次。

`GET .../{id}/batches/{batch_id}` 可补领历史批次。权限每次重检，历史公开数据改为其他客户私有后旧批次也不可读取/确认。此时可以 `POST .../{id}/batches/{batch_id}/discard`，正文 `{}`，释放阻塞而不推进账本；下次拉取会重新筛选权限。客户端按 event_id 幂等消费。

当 batch_id=null，看 reason 和 next_poll_at。积压批次确认后可立即拉取；清空后遵守 interval_seconds。服务端无后台推送，客户端需定时调用。扫描通过“未确认请求反连接”发现迟到提交，不靠 completed_at 高水位，因此旧时间戳的新入库记录仍可交付。对原记录的就地修改不作为新请求事件，本轮补链使用独立证据接口。

工具 `python -m scripts.pull_data_subscription --base-url https://searchflow.20-150-145-162.sslip.io --subscription-id <ID> --output <目录>` 会下载并验证JSONL/CSV及清单、原子保存后才确认。默认从本机权限600凭据文件读取，不输出Key。可由用户自己的定时器每分钟运行；不会自动恢复暂停订阅。网络失败重跑同一命令。

## 历史新闻补链

`python -m scripts.backfill_news_links --output <新证据文件>` 默认离线预览；`--apply` 只写独立 sf_news_link_enrichments / sf_news_backfill_progress，不改原始响应、URL registry、原请求摘要或历史新链接贡献。`--online --apply` 才访问Google解析入口，复用共享限速/冷却/查询锁；绝不访问出版社正文。

每轮有请求数、链接数、墙钟预算及 pause-file，支持重跑。deferred 保存下次重试时间；未到时间跳过，避免阻塞后续请求。unresolved 保留失败依据，通过 `--retry-unresolved` 显式安排重试；`--retry-seconds` 默认300秒，指数退避最长24小时。resolved 证据不覆盖。在线解析不是默认调度，不自动批量访问55万条历史新闻。

`GET /v1/data/requests/{id}/news-links` 返回按现有客户权限过滤的独立补链证据，包含 wrapper、target、解析状态、时间、attempts 和 next_retry_at；原数据与标准快照保持不变。离线不能解码的包装链接继续明确未知。该端点不代表出版社页面核验。

## 验收边界

不启动新搜索、正文、Whale上传/领取或监测；不运行历史正文/hash迁移。生产只更新十个后台文件（含采集端同版本解析器依赖）及静态页面，保留受保护 web.py/serp_page.py 等及采集机五容器。具体测试、发布、生产状态和未完成项见本轮执行记录；不能把局部测试称全量历史补链、七天可靠性或百万行验收。

## 进度和记录接口（本日第二轮）

- `GET /v1/data/subscriptions/{id}/status`：同一只读快照下精确 remaining_requests（包含待确认批次）、pending_batch、已确认请求/事件、最近确认及批次存储量。存储量是批次大小指标，不是数据库磁盘占用或配额。
- `GET /v1/data/subscriptions/{id}/batches?limit=20&cursor=...`：1–50条轻量元数据分页，含pending/acknowledged/discarded，不扫描完整历史payload。下载仍重检当前权限。
- 完整请求上下文可能含重复链接，增量消费以events为准。网页“从现在开始”不会开启自动采集，订阅仍是客户端定时拉取。

第二轮仅更新五个后台模块和静态页；新增只读七天归档巡检。上线与实际补链样本见 [第二轮执行记录](data-subscriptions-next-release-2026-11-10.md)。
