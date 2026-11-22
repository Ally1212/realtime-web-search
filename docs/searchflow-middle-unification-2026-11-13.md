# 全链路统一迁移进展（未完成）

验收目标：退役旧上传/领取实现，新增搜索统一中台派单、持久化回执、固定版本读回和下载一致性；保留历史业务数据。

## 当前新增实现

- `middle_maps_contract.py`：按中台11月12日回复生成conditions；位置与类型能力；独立查询快照消息；零结果合法；固定版本/载荷/实际SHA256校验。尚未接入Maps worker。
- `middle_live_runtime.bind_live_observation`：Web新消息的external_content_id绑定query/task/原URL身份，避免继续仅按URL复用文档身份；同请求重试稳定。此改动是防止跨查询文档复用的措施，不代表已经证明中台会按此身份创建独立版本。
- 本地单元测试：Maps契约5项，Web运行器及身份测试7项通过；此前客户端/Collector/readback相关测试通过。尚无本轮生产存取成功证据。

## 现场读取

11月13日通过SSH读取采集机：`searchflow-middle-live-1`、Maps服务存在且运行。state/private文件名核验有现有Consumer与运行环境文件，未发现以Maps命名的专属凭据文件；不能仅凭文件名判定授权是否开通。未输出密钥。

## 必须继续

1. 核验最新中台OpenAPI、现有Consumer授权及Maps专属凭据配置，不把回复的“测试环境已发布”当作生产授权已开通。
2. 补Maps持久化阶段状态、专属任务注册/领取、回执、snapshots按version读回，接入现有API和导出。
3. Web真实重复URL及新URL测试；处理429退避和明确失败终态，确认身份调整能解决版本复用现场问题。
4. 迁移News/Images/Trends，检查中台支持来源及契约；不得静默回退为本地成功。
5. 抽离新运行器仍从whale_serp复用的公共能力，再删除旧队列、旧运行器、路由、配置及专用测试/部署入口；不删除历史数据。
6. 正式保存回执、固定版本原始载荷与hash、租户隔离、幂等、额度及实际导出深比较证据，才可报告完成。

本轮未发布、未删除旧代码、未开启持续采集或正文。

## 依赖拆分进展

新增middle_ingest.py，移动消息构造、回执校验、任务协议及Collector传输公共能力；旧whale_serp暂时重导出，待调用者迁移后删除旧队列实现。新middle_live_runtime已不导入whale_serp，改用CampaignStore和run_serp_campaign；采集配置明确whale_serp_enabled=False，避免旧任务自动上传挂钩。

相关回归63项：40通过，23跳过（test_whale_serp真实隔离PostgreSQL测试缺TEST_DATABASE_URL）；compileall通过。跳过部分不能当作验证通过，仍需准备隔离数据库运行。所有变更尚未部署。

## 中台现场复查与Maps客户端

通过生产独立运行器现有Consumer调用后端API，只读复查11月12日失败查询：平台仍succeeded；问题document的`/snapshots?limit=500`返回`snapshots: []`。这不证明本次载荷可读，也不能以平台succeeded替代交付验收。未新增采集或上传。

新增MapsMiddlePlatformClient，使用google_maps_raw/google_maps；创建和查询状态核对原始位置/类型条件、语言与地区；快照接口按固定version匹配且核对数据集、来源、document、完整payload和实际SHA256。拒绝缺失、重复版本和篡改。相关Maps/中台客户端/一次性采集回归40项通过，无跳过。尚未接入Maps worker或发布。

一次性采集模块已改从middle_ingest导入回执/退避公共工具，减少旧模块依赖。

## 浏览器配置进展

用户明确授权直接通过浏览器创建所需中台配置。ego独立任务空间294，p1；当前组织所有者账号已核验。创建SearchFlow Maps应用成功，ID consumer_d39dec56a6a1f90ec7357cdee4eddecf，列表状态正常，尚无Key/调用。旧SearchFlow Google Readback限制页已有Google地图来源选项，但数据集按钮只有原六项，未出现google_maps_raw；尚需找到数据集/Collector管理入口，不能将新建Consumer应用视为数据集和采集授权已经开通。任务未结束，保留同一浏览器空间继续。

Maps Consumer Key已通过ego页面生成，名称SearchFlow Maps server，列表显示正常；密钥仅保存state/private/maps-middle-consumer-key（600），未输出或提交。尚未授予/核验Maps数据集权限，不能凭Key正常宣称业务可用。

新增task_parameters校验已领取Maps任务的task/query/dataset/source/能力及完整conditions/语言/地区，拒绝其他任务或半径篡改；Maps相关8项本地测试通过。

## 管理后台登录与原始快照

ego空间294新增p2导航/admin，实际跳转/login：需要内部管理员用户名/密码，组织Portal会话不适用。已handOff交用户登录，未尝试猜测或提取密码；后续用户确认登录后takeOver同一空间。不要创建新空间绕过交接。

maps_provider.search现保留raw_response；query_snapshot从原始响应重新规范化并核对商家列表、源响应SHA256，再构造带job/query/task、条件和原始对象的快照（含零结果）。相关53项测试45通过8跳过；无本轮真实入库/读回成功证据。

## 真实PostgreSQL回归补验

本地独立PostgreSQL17（127.0.0.1:55439）UTF8数据库运行whale_serp/middle_live_runtime/middle_maps_contract/maps_contract/maps_provider/middle_platform_client/middle_collector/google_whale_readback：86项全部通过、无跳过，耗时4.045s。日志output/middle-unification-20261113/postgres-regression.log。初始默认SQL_ASCII库导致字符串变bytes/Unicode错误；修正测试库编码后复验通过，未修改业务代码掩盖错误。测试数据库服务已停止，数据目录保留/tmp/searchflow-middle-pg-20261113/data供续测。不是生产中台存取验收。

## Consumer读取限流修复（本地）

WhaleReadbackClient对GET和两个明确只读的batch-get支持最多3次429重试，遵守短Retry-After；长等待保存在客户端冷却时间内直接拒绝网络调用，避免阻塞或提前重试。写入路径不重试。新增成功重试、次数上限、长等待和写请求不重试测试；相关29项通过。未部署；进程重启后的全局冷却持久化仍需在协调器完善。

## Maps分阶段运行逻辑

新增middle_maps_runtime.MapsPhases：创建中台查询→绑定任务并校验条件→采集并保存完整消息→回执查询→realtime上传→accepted/duplicate固定版本绑定→complete→Consumer结果身份校验→固定快照读回。每次写副作用前保存状态，queued不能交付，重启已保存消息不重采。12项Maps阶段/契约测试通过。此模块尚需接SQL持久状态、专属Collector transport/注册心跳、队列期限及本地worker；不能当作正式集成完成。

新增middle_maps_collector.MapsCollector：专属凭据，注册仅google_maps/live_query及位置/类型能力；realtime header入库，不自动重试写入，脱敏错误。新增测试与Maps阶段/契约共14项通过。google_local_jobs新增私有middle_state列与仅活动/所属任务可更新的持久化方法；不在客户返回列中暴露。尚未部署该schema或接worker循环。

Maps worker已改调用process_middle_pending，要求显式中台endpoint及专属Consumer/Collector文件，配置不全拒绝启动，不退回本地成功。阶段状态存sf_google_local_jobs.middle_state，恢复running优先于queued；读回验证成功才结算，超时先取消远端再释放额度。采集中增加任务心跳。真实UTF8隔离PostgreSQL及Maps相关29项全部通过、无跳过，包含私有状态恢复和读回后才扣次；测试库已停止。未部署；仍需处理重启租约冲突、失败重试/冷却持久化、后台授权和真实中台联调。

## 旧运行入口退役（本地）

worker.run删除旧WhaleSerpRuntime启动线程；serp_worker删除采集后stage_subscription钩子；serp_console拒绝upload_to_whale=true且删除旧订阅表写入；serp_console_http不再导入旧HTTP模块，对/api/whale/明确410。旧模块本体仍待删除，未宣称全部清理完成。相关45项含真实隔离PostgreSQL全部通过、无跳过；测试中使用默认Config的后台连接池对5432产生拒绝连接日志，实际测试库55439断言通过。日志output/middle-unification-20261113/retirement-regression.log；隔离库已停止。未发布生产。

已删除realtime/whale_serp_http.py和scripts/deploy_whale_serp.py；删除旧HTTP可创建/控制上传队列的过时测试，新增测试证明旧GET/POST URL统一410且不访问数据库。middle_collector测试改从middle_ingest导入协议，相关9项通过。whale_serp队列模块本体仍有一次性采集测试fixture和图片拒绝旧上传测试依赖，尚未完成删除；需继续迁移这些fixture和历史审计读取逻辑。

## 删除旧队列模块本体

已删除realtime/whale_serp.py及其过时队列行为测试test_whale_serp.py；有效公共协议测试迁移test_middle_ingest.py，一次性搜索数据库fixture独立为search_database_fixture.py，图片测试去除已退役队列断言，单次搜索去除旧订阅表依赖。realtime/tests/scripts无旧模块Python导入。真实UTF8隔离PostgreSQL相关60项全部通过、无跳过，5.975s，证据output/middle-unification-20261113/delete-legacy-regression.log。数据库已停止。仍需检查whale_collector旧CLI/正文上传、配置/界面/Compose和旧表读取，生产尚未删除或发布，不能称全仓清理完成。

已删除whale_collector.py中旧WhaleRunner/ContinuousWhaleRunner及调度控制器，删除whale-worker/continuous-whale CLI；Compose collector profile改middle、命令改新middle_live_runtime，不再启动旧运行器。仍被crawler/google_experiment使用的文档消息及传输工具迁移middle_document_ingest.py（无任务运行器），保留其业务实验功能；旧运行器测试退役，保留文档消息契约测试。54项相关测试通过，无跳过，compileall通过。正文实验的手工上传/旧数据库方法、页面和历史审核仍需审查；生产未发布。后台登录仍待用户，未操作已交接浏览器。

全量unittest discover已启动（统一exec session89854），输出/tmp/searchflow-middle-pg-20261113/full.log；当前确认仍运行，不得重复启动。PostgreSQL55439本轮保持运行供该进程使用，完成后清理。测试日志出现默认Config连接5432拒绝，待最终结果定位。

按已保存中台OpenAPI SourceMessage/SourceMetadata/NormalizedDiscovery补Maps消息必填received_at、discovery.collector/acquisition_type=live_query和discovered_at，并校验带时区的实际observed_at；12项相关测试通过。Maps等待回执阶段补任务heartbeat，不仅采集时保活。

全量首次进程89854已主动SIGINT结束（exit130），栈证实test_google_api setUp导入web.Handler时使用默认Config连接5432，非生产中台等待。已同时指定DATABASE_URL和TEST_DATABASE_URL为隔离UTF8库，重跑unittest discover -v，新session84648，日志/tmp/searchflow-middle-pg-20261113/full-utf8.log，当前仍运行且测试名称持续推进。不得重复启动；测试库55439保持运行，完成后停止。此前首次日志保留，不算全量通过证据。

全量session84648继续推进，尚未结束。已定位并修复CapabilitySchemaTests失败：maps_schema重复追加{id}路径参数，改为已有则不再添加，6项相关重跑通过。移除不再使用的Config.whale_serp_enabled及.env.example开关，23项相关测试通过。当前日志还显示google_places_adapter、google_revision_migrate、google_web_once_cli失败/错误，需等完整栈后逐一处理，不能称全量通过。不要重复启动全量进程；55439测试库保持运行。

全量session84648完成：1470项，failures7/errors6/skipped6，非通过；日志output/middle-unification-20261113/full-regression-first.log。修复IdleGuard新安装无旧队列表时UndefinedTable（历史表存在仍核验其开关/活动任务），相关6项通过。RevisionMigrationTests新建数据库继承SQL_ASCII模板，改为明确template0/UTF8，4项通过。规模测试因本机磁盘可用12GiB低于16GiB门槛拒绝执行，不降低门槛伪造通过。另有google_local_http三项JSON响应错误和旧Maps页面断言待处理；OpenAPI重复参数已修。测试库已停止，当前无全量测试进程。

修复product_http的Maps路由吞掉旧Local分析路由问题：local_handle返回False后继续google_http，三项Local HTTP测试恢复。Maps页面测试改验证当前sfSourceData内google_maps入口及mapsView，旧data-view侧栏已随既有UI改造移除；未改UI业务。相关9项含隔离PG通过，无跳过。规模测试12GiB磁盘门槛阻塞仍未解决，不伪报全量通过。

Maps Collector的429解析Retry-After（秒或HTTP日期），不重复写入；process_middle_pending将retry_not_before持久化middle_state，后续轮次/重启恢复等待，避免固定轮询提前重试。新增真实PG测试验证冷却期间不调用phase、reserved仍保留且used不增加。相关22项全部通过，无跳过；测试库已停止。仍待后台登录及真实中台验证，未发布。

删除生产google_local_jobs.process_pending及ProductCoordinator的旧本地Maps调用；存储/导出测试所需结果模拟移入tests/maps_job_fixture.py，不再由生产代码执行本地成功路径。Maps专属中台worker为唯一Maps执行入口，未配置拒绝启动。相关Maps31项、Product/QuickRequest24项均含隔离PG通过，无跳过。证据output/middle-unification-20261113/coordinator-regression.log；测试库已停止。未发布，无真实中台保存读回通过证据。

新增SearchMiddlePlatformClient/search_request，对Web/News/Trends保留真实来源分别映射google_search/google_news/google_trends，按客户端实例固定来源防串单；Images缺少已确认来源契约则明确拒绝，不冒充Web。middle_collector.live_payload和middle_ingest.task_spec扩展按任务真实来源转换，不再无条件改为Web。相关10项通过。仍未改正式路由或为Feed建立运行器/读回验收，不代表三来源已打通。

LiveRunner现按Config.whale_source_platform映射Web/News/Trends固定来源，SearchMiddlePlatformClient保留来源；campaign与任务登记不再硬编码Web；独立来源PG锁及队列过滤；领取平台与固定版本校验按实际来源；所选News结果/Trends文章保留正确响应字段。新增Feed读回不可冒充Web测试，相关18项通过。尚未配置/启动Feed生产运行器，快速请求路由仍保留原路径，Images契约未确认；不能称全部来源已打通。

## 后续清理与复验（2026-11-13）

删除crawler正文写入/复用两处旧队列钩子，以及CampaignStore旧queue/outbox读写重试方法；record_page移除旧上传参数、旧outbox插入及上传清空正文逻辑，普通本地正文保留功能继续验证。旧表历史读取/初始化及历史实验上传仍有残留，不能声称全仓清理完成。

Maps初次任务heartbeat统一检查cancel/stop/pause，控制信号下不搜索、不上传；worker取得PG领导锁后才注册，空闲每15秒发送心跳，防止闲置后能力离线。新增初次控制信号、重复worker不得注册以及空闲心跳测试。

本轮全量项目虚拟环境测试1475项，221.208秒：1 error、6 skipped；唯一error为规模测试磁盘不足16GiB门槛，6 skipped均为未提供TEST_REDIS_URL。证据full-regression-second.log。之后启动本任务独立Redis56389补跑队列及最新Maps/正文清理回归50项全部通过、0 skipped（2.178秒），证据latest-targeted-regression.log。全量运行期间有上述后续修改，因此最新改动以50项针对性回归为证，不宣称最终全量绿灯。首次误用系统Python缺依赖报错后已改项目.venv，无业务改动掩盖环境问题。compileall与git diff --check通过。

后台仍处于交接用户管理员登录状态；未恢复浏览器操作，未部署生产，未完成Maps真实中台保存/固定版本读回，未宣称所有来源统一验收。

## 历史正文实验上传清理与客户路由统一

删除google_experiment实验消息/flush_whale上传重试/队列写入/远端回执后丢弃正文，删除fast_experiment上传线程及旧待上传数对调度和排空的控制，CLI移除--whale/--remote-only。删除仅为旧上传保留的middle_document_ingest.py和其契约测试；本地实验保存与导出仍保留。旧上传行为测试退役，新增旧配置whale/remote_only=true也不能队列写入或丢弃正文的测试。历史SQLite表、统计及baseline只读仍留存，未清除历史业务数据。

客户单次/v1/search、批量/v1/search/batch和网站quick-request均使用middle_search_payload：Web/News/Trends固定middle，拒绝显式openserp/serper 410，不允许Images静默落回本地。Product参数放开middle Feed；OpenAPI新搜索来源移除尚无中台契约的Images，provider仅middle。主capabilities提供方同步middle及固定版本读取要求，Images保留历史能力描述但明确new_requests_enabled=false。未发布生产。

真实隔离UTF8 PostgreSQL下Product/QuickRequest/Maps/Feed/实验回归129项全部通过、0 skipped，4.011秒；日志unified-routing-regression.log。之后capabilities调整相关19项通过。compileall与diff检查继续通过。待办：前端旧provider选择与示例、监测/操作台旧本地提交路径、旧配置与历史队列表初始化/只读边界继续审查；Images来源契约仍未确认；Feed运行器尚未部署和现场验收；Maps后台授权与真实中台保存/固定版本读取仍待管理员登录。不得以本地测试通过称目标完成。

## 监测与目录清理、全量第三轮

新建监测只接受middle；旧监测保存的provider在调度时迁为middle，新任务保持原查询/地区/语言，不重启旧持续采集。真实PG测试覆盖旧openserp配置调度得到middle任务及暂停释放额度。API页隐藏provider默认值改为middle，防止页面继续提交退役提供方。相关28项通过，无跳过。

在隔离PG55439/Redis56389下完整运行1475项、210.697秒，0 skipped、1 error；唯一error仍为规模测试要求16GiB空闲磁盘，本机不足，不降低门槛。其余1474项通过；不能称全量通过。日志full-regression-third.log。

随后删除customer_schema仍宣称可用的/api/whale/status/settings/jobs/操作及上传请求体，删除Config无人使用的whale_enabled/continuous_whale_enabled/whale_claim_limit/whale_max_concurrency；.env移除旧启动命令、失效开关及误导启动注释。保留新live运行器实际使用的服务端凭据和协议配置。相关25项通过、无跳过，日志schema-retirement-regression.log；compileall和diff --check通过。

只读正式API核验新Maps Consumer Key：按OpenAPI正确source_platforms参数GET /v1/documents/browse?dataset_id=google_maps_raw&source_platforms=google_maps&limit=1返回200，证据maps-read-authorization.json（仅状态和错误code，无Key）。初次误用单数source_platform返回400 invalid_document_query，修正参数后复验。200仅证明接口接受Key，不能据此证明dataset/snapshots/live权限已开通或有数据，更不是写入/固定版本读回成功。未操作已交接的浏览器；Maps Collector仍未创建配置。

本轮无生产部署/真实采集，中台保存与读回最终验收仍未完成。旧产品协调器本地执行、内部submit的本地分支、旧操作台创建入口及历史表配置边界需继续收敛。

## 产品旧执行分支彻底退役、失败契约与资源补测

ProductService删除本地SERP成功结算、重新入队、Serper直连及run_serper；遗留在途非middle任务标记provider_retired、停止关联campaign并释放预留，不能凭本地结果充当中台成功。ProductStore参数默认middle且只接受middle；删除旧campaign及serp_task_options创建分支，内部调用也不能绕过入口。旧任务历史读取仍保留。历史测试样本统一tests/product_job_fixture.py，直接构造旧私有记录，仅测试依赖，不在生产保留旧执行分支。新增明确验证旧provider在预留前拒绝。相关66项通过，compileall/diff检查通过。

完整第四轮：1476项、195.198秒，1475通过、0 skipped；唯一error仍为本机规模测试16GiB空闲门槛。证据full-fourth.log。后续Maps新增一项失败恢复测试不在这轮全量范围，独立相关35项全部通过、0 skipped，0.980秒，maps-failure-regression.log。

Maps采集/解析失败新增collection_error持久化：错误先保存，下一阶段调用中台任务fail（retry=false），失败回执未知也不重采、不写零结果；中台终态保留原失败code，再取消查询并退款。对照中台回复“超时或解析失败调用fail”，不把取消等价冒充fail。测试覆盖timeout/invalid_response及fail传输未知后恢复、不上传/不重复搜索。

为解决资源门槛，采集机只读检查可用29GiB，再使用本任务专属临时目录与两个独立资源限制Docker容器（现有镜像，未拉取新镜像，独立PG17，127.0.0.1:55439）。复制候选代码，不挂生产目录/密钥，不访问生产PG，不执行新采集或上传。test_google_data_scale_probe三项（含此前阻塞的303条合成数据混合来源导出/私有隔离/只清理自有schema）0.690秒全部通过、0 skipped；没有降低16GiB门槛。证据remote-scale-regression.log，候选SHA81c2131397ecf9fe2f20ef13a7064efd61067c48688b267d7cb34cd013035dec，远端与本地归档SHA一致。测试两个容器和临时目录全部删除核验，remote-scale-cleanup.json。合成规模测试不能证明真实中台保存或百万行真实交付。

尚未生产部署。真实Maps Collector/数据集授权仍未完成，浏览器仍交接用户管理员登录；各来源中台保存/固定版本读回/真实导出一致性未完成，Images来源契约未确认。不得标记总目标完成。

## 实时任务类型收敛与Feed地区修复

middle_ingest.task_spec仅接受live_query，删除keyword_search/backfill/content_detail解析及旧队列形态claim/task_heartbeat/complete方法；注册默认与LiveRunner配置仅live_query。保留真实协议fail方法。News从请求region/language映射news_locale，Trends明确设置trends_geo，避免新路由仍按默认地区采集。注册metadata只声明本运行器真实来源。LiveRunner取得独立来源领导锁后注册，空闲每15秒心跳，避免中台在线能力检查因闲置失效。新增旧任务即使旧配置允许仍拒绝、Feed地区/语言选项、单领导及空闲心跳测试。

联合回归在真实隔离UTF8 PG下65项全部通过、无跳过，1.275秒，证据live-source-final.log；初次未带数据库变量跳过18项，已补环境完整复测。compileall/diff检查通过。本轮未部署或操作已交接浏览器，真实中台保存/固定版本读回仍未完成，管理员授权及Images契约待处理。

用户已明确完成管理员登录并交回控制。通过ego takeOverTaskSpace(294)核验p2位于/admin且显示管理员导航；已创建并读回确认active数据集google_maps_raw（Google Maps查询快照），更新时间2026/11/13 13:33:05。下一步创建Maps专属Collector Key并配置Consumer数据集/live/snapshots授权，尚未真实写入验收。浏览器任务继续保留，不再处于等待用户登录状态。

已通过管理员UI创建SearchFlow Maps snapshots订阅集，仅google_maps_raw/google_maps，字段identity/title/metadata/platform_raw/metrics/snapshots，1RPS/并发1；分配至consumer_d39dec56a6a1f90ec7357cdee4eddecf，计划searchflow-maps-pinned-readback，刷新读回active。已创建专属Collector Key cak_07f570d847d1ee16349bc9794ce06114，仅maps数据集/google_maps/live_query，明文安全保存state/private/maps-middle-collector-key（600），未打印。实际注册成功，agent collector-google_maps-prod-756ec8bd。首次live-query coffee SG Singapore radius1500请求403 entitlement_denied：live query is not authorized for dataset=google_maps_raw platform=google_maps。相同幂等键复验仍403；未创建成功查询，未采集上传。证据maps-live-admission.json，私密状态maps-live-validation-state.json。继续检查中台订阅与实际entitlement的差异，不能认为授权已全部生效。ego294 p2当前/admin/collection采集器Key页，显示一次性Key，后续截图/快照必须脱敏或先导航；p1保留Portal。

已完成Maps实时授权修复：org访问策略显式live_query_enabled=true，日限100/单平台条数21/实时并发1；应用ent_37ff9d533e12c778b160c51987010d84原rate_limit_policy缺live_query_enabled，通过已登录ego页面PUT /api/admin/access/entitlements/{id}保留原scope并增加同样live限额，200。只改组织策略仍403，故应用授权缺失是已验证关键原因；不能将早前UI复选框观察称为唯一原因。实际MapsCollector重新注册后同幂等键创建成功：lq_cb06fa5ce5021fc7726d488811a3c8c6/ctask_6a82df8ec9cc44c06a8cc436dc1c8336，pending；授权探测随后cancel成功，避免留无人处理任务。未采集/入库，不能称保存读回通过。证据maps-live-admission.json、maps-entitlement-before.json。后续使用新幂等键正式采集测试。

小范围真实Maps测试：独立临时容器运行当前provider，coffee/Singapore/1500m，20家商家，仅1次真实采集；中台lq_8ef1feb10370b4dd648c536d4e5a04b1成功，accepted回执doc_b38ade4ac44632bb632759d76a27e44f/ver_c0227e7878ff495872355021e46e7d74。versions可读取到同版，正文size0符合契约，但snapshots多次返回[]，完整载荷读回失败，未宣告可下载/扣网站额度。Consumer限流现场暴露连续读取429，客户端增加2.1秒请求间隔，相关24项通过（71.527秒）；仍需进一步完善跨客户端限流。保存状态后结束原探测进程，恢复时search回调明确禁止重采，没有重复采集。幂等重放同query通过，变更radius仍用原key返回409 idempotency_key_conflict；第一次误遇429导致false已用真实409复验修正。/content因未授body返回403，不扩大正文权限；该接口不是Maps载荷读回替代路径。证据maps-small-test-result.json、maps-snapshot-diagnostic.json、maps-versions-diagnostic.json、maps-real-idempotency.json。独立Maps容器自动删除及临时目录清理核验完成；尚未部署网站/开启自动采集。零结果/完整网站导出及全部来源现场验收未完成，需中台快照缺失问题处理后继续。

用户指出异步入库/读回需要时间，已在2026-11-13 06:00:34 UTC（新加坡14:00:34）重新读取同一批数据，无新搜索/上传。原消息05:47:53 UTC accepted已约12分40秒，仍accepted；补报05:56:06提交已约4分28秒，仍retrying/persistence_retrying，最后updated05:58:26。快照现2条，同版本均payload_hash空、platform_payload缺失，固定版本完整读回仍false。不能仅凭延迟推断永久故障或已证明根因；目前只确认等待后仍未完成可验证读取。证据maps-delayed-readback-recheck.json。

间隔复查重要进展：2026-11-13 06:07:15 UTC补报回执从retrying变accepted，快照数2→3；06:08:19再查仍原消息/补报均accepted，但3条快照同版本的payload_hash均空、platform_payload均缺失，完整固定版本校验仍false。第一轮5次定时检查进程正常exit0，不是观察超时误判结束；继续下一轮只读间隔检查。证据maps-periodic-checks.jsonl/maps-periodic-latest.json，无新搜索或上传。

发现读取路径差异：/v1/snapshots/payloads/batch-get（官方OpenAPI明确只读）按原snapshot_id/snapshot_at返回完整20家platform_payload，而per-document snapshots仍缺载荷/hash并重复同snapshot_id三行。批量返回所有原始字段逐字段完全一致，但额外添加platform_metrics={place_count:20}，因此整份JSON摘要不同，不能标记严格hash验收通过。证据maps-payload-batch-diagnostic.json、maps-batch-readback-comparison.json。下一步应按此实际契约在新消息原始payload中明确包含同样的真实platform_metrics及metrics快照，再做有界小范围真实查询和固定版本/载荷hash/导出一致性；客户端如增加批量只读回补必须绑定snapshot_id、document_id、固定version和version.raw_payload_hash，不能放宽校验。

本地适配器已补：query_snapshot原始payload明确platform_metrics.place_count，snapshot_message同时携带metrics.snapshot_at及platform_metrics真实count；不再依赖中台事后补字段造成摘要变化。Maps读取缺载荷时调用官方batch-get，只在document_id/snapshot_id匹配、版本列表唯一版本的raw_payload_hash等于本次提交摘要时合成校验输入，最后仍严格逐字段及实际digest校验；不删除实际返回额外字段冒充匹配。同一有id快照的逐字相同重复行可去重，无id重复/冲突多行仍拒绝。新增3项hydrate正常/异snapshot/额外字段篡改测试，相关17项全部通过14.739秒。证据maps-hydration-tests.log。未部署，需新小范围真实查询验证原始消息与读取路径，旧补报payload加字段导致的严格hash不匹配仍保留为失败证据。

最终Maps v2小范围真实测试：20家与0家样本均accepted、固定版本完整payload及实际hash严格一致；两次幂等重放同query/succeeded，CSV/JSONL与中台读回内容深比较通过。临时容器与远端测试目录清理已核验。报告searchflow-maps-roundtrip-passed-2026-11-13.md。仅本次用户新目标“间隔检查直到测试流程通过”的Maps小范围流程可验收，旧目标已由用户清除；不要称全项目迁移或生产部署完成。

## 正式发布更新（2026-11-13）

新实时查询适配已部署正式网站并完成Web 3条、News 10条、Trends 10条、Maps 20家正式存取、固定版本校验和下载深比较。此前未部署的描述为历史阶段。Images沿用原流程；原始历史和原采集管理任务不在本次迁移范围。正式验收发现并修复Trends跨话题重复报道的计数问题，保留首测失败。详见[正式发布说明](searchflow-middle-production-release-2026-11-13.md)。
