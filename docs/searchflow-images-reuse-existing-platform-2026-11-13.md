# 图片复用已有中台平台与资产字段

用户修订：不再要求独立中台google_images来源。平台复用google_search，dataset沿用social_media_raw，本地source保留google_images。

## 实现

- SearchMiddlePlatformClient使用现有Google平台和通用conditions，条件other/searchflow_vertical/eq/images；这不是中台新增字段，命名由SearchFlow解释。正式OpenAPI和collection-modes明确允许other/eq字符串、原样透传及condition:other能力匹配。
- Google Collector注册、领取共同支持assets/condition:other；任务转换按本地来源严格匹配条件，Web拒绝非空图片条件。平台多对一后不再使用反向字典猜测本地来源。统一Google运行器共用平台锁11与Collector，轮询Web/Images两类任务，每类使用固定source的执行对象。领到另一条已绑定的本站任务时保存到其所属job，下一轮执行；其他应用任务通过fail(retry=true)归还。禁止再部署两个独立Google运行器抢相同平台队列。
- 继续复用assets(media_type=image,role=media)，精确图片URL、缩略图、已知正整数尺寸；固定平台载荷同时绑定资产字段用于hash校验。没有新建平台、数据集或存储API。

## 正式接口证据

- 现有Web凭据对同平台图片消息dry-run返回HTTP200/valid=true，不入库。
- 新建最小Collector Key，仅social_media_raw/google_search/live_query；key id cak_daa212c8fb86bc98de714172a5a09b06，agent collector-google_search-prod-15e5d081。明文只在state/private/images-google-collector-key，600，不进文档或代码。
- 图片条件实时查询lq_ee7c641c0d039d2565c1cbd6bba731c4创建成功，验证条件未丢失，随即取消；未采集。未修改现有Web实例注册能力。
- 从服务器只读取得一条已有Google Images真实结果（20张），选1张历史观察提交，原采样时间保留。第一次校验因脚本把数据库时间串保留空格而400；改为RFC3339后校验/入库成功，accepted回执、固定版本完整载荷/hash及CSV/JSONL深比较通过。
- 独立读取标准metadata.assets，服务端补充空字符串/0/空对象字段，故整体字节不等；逐个已提交字段全部一致，未把当前metadata当作固定版本证据。

证据output/images-reuse-20261113/：ingest-dry-run.json、condition-admission.json、real-image-message.json、real-image-receipt.json、real-image-readback.json、real-image-document.json、roundtrip-proof.json、real-image.csv/jsonl。只有一个历史图片样本入库，不代表20张都上传或新实时搜索全链路验收。

## 发布边界

本轮无服务重启、网站部署、自动采集/正文开关变更、新搜索或原图下载。正式图片入口仍沿用既有流程；本地Product提供SEARCHFLOW_MIDDLE_IMAGES_ENABLED=true发布开关，默认关闭；启用时深度20、provider=middle，capabilities与真实路由一致（不得单独开放而没有统一Google运行器）。剩余上线工作：隔离端到端新查询验收、候选发布与入口切换、正式刷新/结果/下载验收。无需再请求中台新增来源。

相关回归43 passed/60 subtests；完整回归日志full.log另记环境限制。compileall和diff检查通过。既有工作树改动保留，未提交Git。


独立审查提出共享平台claim互抢问题，已按统一分派修复；新增混排绑定与陌生任务归还测试通过。最终图片专项13 passed。新运行器替换部署时须先排空/取消旧google_search在途任务，避免旧Collector绑定的租约与新Key冲突；不启动第二个相同平台运行器。新Collector Key留私密配置备用，本轮未挂载生产。

浏览器334已finish，探测live query已取消，历史样本accepted保留审计。全库结果与环境限制见final-full.log；未宣称新实时图片查询全链路上线。

最终验证（统一分派修复后）：图片专项14 passed；相关36 passed/15 subtests（新增共同锁测试另通过）；全库911 passed/618 skipped/642 subtests，7 errors均为tests/test_google_legacy.py::LegacyPreviewTests初始化缺TEST_DATABASE_URL，0断言失败。全库未全绿，不能宣称无跳过完整验收。中间两项旧闲置测试因新循环读取未提供的fixture配置报错，已通过非Google路径复用原执行对象修正，最终上述结果包含复验。日志final-full-v2.log/final-targeted-v2.log。
