# 数据交付修复与订阅发布记录

## 完成范围

五项已确认问题均修复：排名显式format=json/csv、网页两层认证示例、九项图片字段目录、service_error筛选、国家大小写。未扩大CORS或客户权限。

新增客户拉取订阅、默认暂停、筛选与两种事件模式、幂等创建、并发单批次、未确认重试、SHA256及完整JSONL/CSV文件清单、原子确认、历史补领、丢弃阻塞批次、迟到提交处理。网页在API工具内提供保存、暂停/继续、领取、下载和确认。客户端脚本校验并原子保存文件后才确认。

历史News提供独立限量补链工具、暂停文件、共享冷却与查询锁、进度和独立证据接口。原始响应与历史首次贡献不改。生产仅验证离线预览，未运行55万条历史在线回填；预览两项无法离线解析，如实返回deferred，未新增网络请求或补链写入。

## 发布与保护

后台实际十个文件：customer_data.py、customer_http.py、customer_schema.py、google_legacy.py、product.py、product_ui.py、customer_subscriptions.py、news_backfill.py、subscription_ui.py，以及从生产worker取得的同版本google_news_links.py工具依赖。

仅新增独立sf_data_*与sf_news_*表。未运行历史正文/hash迁移。只重启专属google-workbench-backend-web-1/prd-1；采集机五容器未重启。web.py、serp_page.py、serp_console.py、google_http.py哈希不变。应用机静态index.html有逐版本备份，无需重启共享Nginx。

第一次安装因精简后台缺少解析器依赖，在导入预检查阶段失败并自动恢复文件；未替换静态页。随后采用延迟加载并补充与worker完全相同的依赖，通过固定候选包路径的隔离测试再发布。线上发现新列表把字符串传给现有table组件的问题，已补测试并修复UI模块与静态页；没有重复创建示例订阅。

远端回退位置：采集机独立后台state/subscriptions-20261110-v2/backup；首轮失败记录在state/subscriptions-20261110；UI补丁前文件另存subscription_ui.before-fix.py；新增google_news_links.py为独立补入文件。应用机state/subscriptions-20261110/index.html.before与index.before-list-fix.html。恢复后台原文件、移除本轮新增文件并恢复静态页面即可回退；sf_*数据保留，禁止重放带marker的旧安装脚本。

## 验证

最终本地完整回归 **1098 passed、333 subtests、0 skipped**；固定候选包路径的独立回归 **40 passed、3 subtests**，含网页组件与下载客户端校验。记录见state/subscriptions-20261110/full-verified与frozen-verified，均使用独立PostgreSQL/Redis；测试子进程结束后由脚本清理。

线上读取8项：字段目录、service_error、US/us一致、排名JSON/CSV、订阅列表均200，缺客户Bearer为401。真实已有关键词solar energy research排名CSV为200、139条数据行。网页示例均包含站点访问头。

通过网页创建一个默认暂停示例，验证继续、拉取、三种下载、确认和再暂停。实际批次4个本人Web请求、18个URL事件；JSONL与CSV完整记录及事件逐项一致、文件字节数和SHA256与manifest一致。页面自动刷新不清除待确认批次。历史批次仍可读取、重复确认200、暂停后拉取409。

示例订阅名称“示例：已有网页数据（暂停）”，最终active=false；不是新搜索或监测。订阅ID见example-subscription.json；批次ID见download.json。只在这个客户已有数据内验证真实交付，跨租户拒绝、并发、迟到、重复URL、权限收回、损坏文件与超时重试机制由隔离测试覆盖，不冒充多客户生产验收。

发布前后只读快照完全一致：serp_requests39962、pages332768、公开分析39934、旧Whale outbox9；continuous/body/uploads/claims均false，启用监测/活动客户任务/在途lease均0。10个发布文件哈希匹配候选，4个受保护文件哈希不变。生产手机390px无横向溢出。

## 限制

订阅为拉取式，客户端需定时调用；未连接Webhook、邮箱推送，未恢复自动采集。new_urls只表示本订阅首次确认的URL，不是全库新增或文章正文。完整请求上下文含旧链接，计数必须使用events。历史News包装链接仍大量未解析；未知摘要、缩略图、作者等没有编造。未宣称七天、百万行或长期SLA验收。

接入合同与操作示例：[data-subscriptions-2026-11-10.md](data-subscriptions-2026-11-10.md)。凭据仍在权限600文件中，不写入仓库或报告。代码未提交Git。
