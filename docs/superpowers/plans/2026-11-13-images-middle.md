# Google Images 中台适配

目标（用户已确认）：social_media_raw + 独立 google_images；图片地址、来源页面、缩略图和尺寸分别保留，不下载媒体。

实现：复用 SearchMiddlePlatformClient、独立来源 LiveRunner、accepted 回执与固定版本/hash读回。图片使用精确URL身份（不沿用网页URL跟踪参数清洗），最多20条、第一页。保留原有其他来源。

约束：当前登录读取的正式OpenAPI缺少google_images枚举。先提供可测的内部适配；正式客户入口保持未就绪，直到中台契约/权限和真实联调通过。不修改正在被另一任务发布的线上服务，不开启自动采集/正文，不提交既有未提交改动。

- [ ] 增加测试：独立来源、20条限制、图片精确URL身份、所选图片与固定版本载荷一致、现有来源回归。
- [ ] 实现内部请求/任务/运行器适配；客户入口保留契约门禁。
- [ ] 跑相关测试与全库回归，记录实际失败与环境限制。
- [ ] 中台提供来源契约后配置最小权限，真实存取和导出验证后才能开放入口。未取得入口则明确交付阻塞。

## 追加：复用已有图片字段（用户再次确认）

读取正式入库协议已确认SourceMessage.assets/NormalizedAsset可用。不新增中台存储；每张图片用media_type=image/role=media，image_url映射url，thumbnail映射preview_url，仅传已知正整数尺寸。新增assets能力声明，注册/领取/任务验证一致；固定版本platform_payload同时绑定相同assets用于hash核验。缺google_images来源枚举仍独立阻塞上线，不伪装google_search。本轮只改后端适配，无前端变更。

## 最新范围覆盖：不要求独立中台来源

用户已取消独立google_images来源要求。中台平台复用google_search，网站source仍google_images。依据现有LiveQueryCondition通用other/eq字符串契约，通过conditions=[{fact_type:other,name:searchflow_vertical,op:eq,value:images}]传递图片模式，能力condition:other强制匹配；严格校验条件原样保留，网页运行器拒绝该条件。专属图片运行器以SEARCHFLOW_MIDDLE_SOURCE=google_images选择本地模式，配置平台仍google_search；禁止反向字典映射把Web误识别为Images。正常复用assets，不要求中台新增枚举。


## 审查修复：平台共用任务分派

不能部署两个google_search领取者分别猜Web/Images。同一平台共用锁11和Collector，运行器轮询两种本地source；不同任务按持久化query/task ID绑定到其所属job，未知任务明确归还retry，禁止执行其他客户/应用的未绑定任务。增加混排、陌生任务归还、共同锁及双模式轮询测试。Product发布开关默认关闭，必须完成新查询端到端和部署才启用。
