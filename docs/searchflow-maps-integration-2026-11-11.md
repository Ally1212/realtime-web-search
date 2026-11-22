# Maps 自建商家搜索上线

## 结果

正式入口：<https://searchflow.20-150-145-162.sslip.io/#maps>。

网站手动查询 → 独立 Maps 队列 → gosom v1.18.2 HTTP fast mode → 私有结果快照 → 网页、CSV / JSONL 已真实跑通。无需用户填写客户 Key，无需 Google Cloud 或第三方付费服务账号。现支持预设城市或自定义坐标、关键词、半径和语言；默认新加坡、5km。每次最多约21条，按距离排序；不承诺完整城市覆盖、原始 Google 排名或完整评论。

**尚未接入 Marsyoo 中台。** 当前线上 `/openapi.yaml` 的 `SourcePlatform` 枚举有 google_search/google_news/google_trends，没有 google_maps；实时任务文档也没有 Maps 专用位置契约。未把地图冒充 google_search，不改已有 Google Web 中台运行器。能力接口明确 `middle_enabled=false`，结果快照 `middle_verified=false`。需要中台增加或明确 Maps 来源、位置条件、数据集和商家对象契约，再实现派单、入库回执及固定版本读回。

## 数据与接口

- `maps_provider.py` 固定官方 Linux amd64 发布包 SHA256，禁遥测、无浏览器/代理/外部网站扩展；每次一个查询、75秒超时、独立临时目录，退出后清理。换行关键词失败关闭，防止一请求变成多个采集任务。没有读取任何用户浏览器凭据。
- 提供名称、地址、类型、评分、评价数、电话、网站、经纬度、距中心距离、营业时间和原始 source_fields。`data_id` 与 `place_id` 分开；本模式缺少 place_id，保持 null。上游缺省评分/评价数0不直接冒充真实观测0。网站只接受 HTTP(S) 链接。地图按钮是根据名称和坐标生成的查找链接，不是声称抓到的商家详情 URL。
- 地区字段为请求元数据；实际位置范围由中心点和半径决定。商家类型为关键词提示，不是假定 Google 官方类型过滤。城市使用显式预设坐标，不是任意城市自动地理编码。
- 独立结果缓存24小时，到期读取/下载410，运行器定期清空。原始字段、查询条件、响应hash、provider版本随快照保留。没有写入公开 SERP 数据或永久 place_id 引用表。MIT 是代码许可，不等于数据再分发许可。
- 网页 `/api/maps/capabilities`、`POST /api/maps/jobs`、`GET /api/maps/jobs/{id}`、`/results`、`/export?format=csv|jsonl`。由服务端空间认证；同源/Quick头校验，任务HMAC收据保护读回。前置站点访问门仍生效。
- 客户 Bearer 对应 `/v1/google/local/*`，同样按租户隔离。旧 `/v1/google/places/{place_id}` 详情接口明确409，不再调用官方 Places。
- CSV 每商家一行，保留完整原始字段JSON和查询条件JSON，普通单元格防公式注入；JSONL为一条完整任务/结果信封，包括零结果时的请求元数据。两种文件结构不同，不能把信封行数当商家数。

## 队列与额度

复用 `sf_google_local_jobs`，仅新增 started_at 列和原有独立 Maps 表初始化，没有运行旧正文/hash迁移。单运行器会话锁；SKIP LOCKED 每次领取一任务。每空间最多两个Maps在途，且与网页搜索共用空间并发和月额度；全平台新任务月上限继续生效。

提交幂等、原子预留、成功扣一次、失败释放预留；运行中断超过3分钟或排队超过5分钟时失败退款，不自动重采旧任务。真实执行有75秒子进程上限；恢复清理基于 started_at，避免把等待时间当运行时间。网站已取得任务收据后刷新恢复，不新增采集。取消任务暂未提供。

## 实际验收

通过 ego-browser 在正式网站点击搜索 `coffee in Singapore`，新加坡圆心 `1.29027,103.851959`、半径5000米、英语。

- 真实网站任务 `f793d52d-6b00-42e6-9f59-860bcb4fa42a`：succeeded，17家商家，charged=1，最终空间 reserved=0。包括 Butler Koffee 等真实返回记录；未逐家独立人工核验商家资料。
- 真实点击并保存 CSV / JSONL；JSONL完整对象与接口深比较一致，CSV17行 source_fields_json 与 query_conditions_json 逐行深比较一致。
- 刷新恢复17家及原查询；错误收据404、缺Quick头403、无站点访问凭据401。桌面与390/320px页面检查通过，无横向溢出。
- 相关隔离PostgreSQL测试57 passed、52 subtests passed、0 skipped，覆盖租户隔离、幂等冲突、共享并发、失败/中断退款、缓存过期、导出、来源字段及已有 Web 中台链路。

证据目录：`output/maps-integration-20261111/`，含 website-data.json、website-download.csv/jsonl、download-verification.json、website-checks.json、桌面/手机截图及发布前后状态。查询条件与原始商家字段保留；没有保存认证Key。

## 发布与回退

采集机新增 Compose 服务 `google-workbench-backend-maps-1`，restart unless-stopped，无监听端口，512MB内存、0.5CPU、64PID、只读文件系统和64MB临时目录；只挂载冻结代码、Maps配置和采集二进制，没有挂载用户凭据。数据库通过已有回环连接访问。

- 后端备份、冻结代码、二进制和SHA256清单：`/home/azureuser/projects/google-workbench-backend/state/maps-integration-20261111/`。Compose前版也在backup，权限600。
- 新增 maps_provider/maps_export/maps_worker；更新 google_local_jobs/http；product.py 仅在线上版本补共享并发检查，product_http.py 仅补Maps网站路由及接口说明，quick_request_http.py 仅切换Maps能力来源。没有用本地整文件覆盖线上 web.py/serp_page.py。
- 静态页仅替换 Maps section/脚本及其能力说明，保留其他网站改动。应用机备份：`/home/azureuser/projects/google-workbench/state/maps-integration-20261111/backup/index.html`。
- 重启 Web、PRD 和新增Maps服务；原采集worker与Web中台独立运行器未重启。原采集worker启动时间仍为 `2026-11-09T10:34:58.023927439Z`。

发布前后 SERP requests40311、pages332768、Whale outbox14不变；持续采集、正文、旧Whale领取和上传均false，启用监测0。Maps只新增自己表内的一次成功手动任务。最终Maps服务running、RestartCount=0。

回退：先将 `state/maps/config.json` 的 enabled 设为false，等待Maps在途结束并确认reserved释放；停止Maps容器，恢复后端backup中的原有模块和Compose以及应用机index.html，重启Web/PRD。保留Maps表与结果，禁止删除数据库或恢复旧正文/自动采集配置。若必须中断在途任务，应先保留独立运行器完成中断退款，不能停止后遗留预留额度。
