# 单次Web闭环：有界流程控制器（2026-11-06）

本轮实现 `realtime/google_web_once.py`，只在本地用FakeAdapter运行，没有部署或访问生产/上游，没有搜索或平台写入。它是执行器的控制部分，**尚非可运行的生产验收命令**；不向用户请求批准一个尚未接线的工具。

## 固定范围与已实现保护

提案固定 `solar energy research 2026`、US/en、google_web/OpenSERP、1页、1次backend source调用、最多1次单消息bulk；不声称OpenSERP内部只有1个Google/代理尝试，实际次数及缓存状态须由生产适配器保存。Dataset/source/collector必须显式填写，无生产默认值。

私有journal目录700、state600，文件fsync+原子替换、目录fsync；flock避免同journal并发。source/validation/bulk调用前先持久化intent，网络结果不确定或进程中断不重放这些调用；queued等候writer终态，不当作完成；receipt只读最多10次，持久保存已消耗次数。没有自动轮询循环或等待策略；后续真实适配器/CLI还须遵守平台Retry-After和节流。

每次重新读取已保存公开请求；核对UUID、source/query/page、真实gl/hl、完成时间不能早于本次source intent。准备后的message hash不变；不以旧历史样本替代新请求。收到的receipt必须匹配Dataset/platform/source_name/record_key，已知document/version不能变；accepted/duplicate之后独立Consumer固定版本payload逐字段、完整结果核对才标为verified。此verified仅是本次控制流程结果，不是正式ReaderAdmission。

STOP文件在动作间阻止继续；每阶段调用适配器assert_idle验证外部开关与无无关在途任务。调用方计划的后续修改不能改变控制器已绑定范围。这里的authorized_plan_hash是显式批准数据的传递机制，hash本身不创造用户授权；日志也不是账号级全局次数限制。

## 实测范围

19项通过（15控制器+4既有builder），0失败/跳过：一次完整成功、重复调用不重发、未知source/bulk结果不重放、queued轮询上限、拒收、源载荷变动、错误固定版本、错误事件receipt、validation中断/拒绝、历史请求替代拒绝、并发拒绝、STOP、范围与journal重绑定及调用方计划修改保护。全部夹具，不构成生产E2E。

证据：`state/google-data-closure-20261106/web-once/verification-summary.json`。

## 尚需完成

1. 生产冻结依赖下的单次OpenSERP acquisition适配器，明确保存失败/成功、内部请求/代理计数、不得创建可被常驻worker领取的active campaign，停止与租约清理必须独立验证。
2. 已有Writer validate/bulk/receipt与Consumer固定版本适配器，区分unknown与failed；单写未知不自动重发；不能扩大旧授权为新调用。
3. 实际HTTP边界的本机服务测试、生产只读preflight及可审阅的准确执行命令。完成后再请求授权一次新Web搜索及该条保存快照一次上传；没有回答不执行。

对应GO-ID-003、GO-ING-001/003/005与WP-GO-01/02仅补局部工具证据；正式来源/用途/schema/ReaderAdmission、旧异常及七天/真实百万行要求保持未完成。

## Writer/Consumer实际HTTP组件增补（仍未部署）

新增 `realtime/google_web_once_platform.py`。依据本地已保存正式OpenAPI与真实dry-run回包，实现单条validate、单消息bulk、精确event receipt与现有Consumer固定版本读取。Writer/Consumer分别传入不同Key，独立Session；禁用环境代理、重定向和transport自动重试。生产只允许HTTPS无凭据origin，HTTP仅显式允许数字loopback测试地址；错误不回显URL/Key/响应正文，Writer回包限制1MiB。

validate必须dry_run/valid均true且唯一index0结果valid，不把HTTP200当通过；bulk必须唯一且具备document/version；部分回包、多余项、错误事件或固定版本均拒绝。没有注册、领任务或来源调用。该组件有真实HTTP实现，但本轮只连接临时loopback测试服务器。

控制器receipt遇到429/503时保存Retry-After（支持日期）加退避抖动后的not-before，重启也遵循冷却。source/bulk未知仍保留intent，不重放。尚未实现未知写入的人工只读恢复命令，不宣称此路径完整恢复。

最终37项通过（7平台HTTP、16控制器、10既有readback、4builder），0失败/跳过。新增组合测试以模拟source+真实本机HTTP validate→bulk→receipt→Consumer GET与固定version batch读取穿过控制器，且再次调用无新写；它仍不是实际Google/Whale端到端。初次本机HTTP断言遗漏现有Consumer的include_body=false请求参数，已按实际协议修正断言，未放宽固定版本校验。

本轮临时HTTP服务均在测试cleanup中shutdown/server_close/join。证据 `state/google-data-closure-20261106/web-once/platform-verification-summary.json`。未部署、未读生产密钥、未搜索、未平台写入。下一步仍是实际冻结worker的一次OpenSERP请求及公开保存适配器、完整CLI/preflight与停止/租约清理的隔离验证；届时再提交具体授权请求。

## 单次来源/保存适配器增补（仍未部署）

新增 `realtime/google_web_once_source.py`。所有Store明确initialize=false，不触发旧迁移。创建start=false、upload=false、once、1页的暂停任务，永不激活；直接调用既有SearchDiscovery._attempt一次，绕开discover_many/page重试与队列，不调用正文。保留共享源/代理节流。动作前独立idle_guard并核对本任务仍paused/started=false/无上传订阅；操作员若激活任务则停止，不覆盖其设置。

专用OneBackendSession只允许配置OpenSERP origin下的一次GET /google/search，禁止重定向、额外路径（含结果URL解析）、环境代理和transport自动重试。OpenSERP自身内部代理次数/缓存仍以实际回包为准；不把一次backend HTTP冒充一次Google尝试。既有SerpStore保存成功或失败；保存失败、未产生唯一结果或停止时不追加搜索。可指定私有source-proof.json持久记录实际attempt摘要/请求UUID；不记录代理URL、凭据或完整请求URL。

隔离PostgreSQL+真实loopback OpenSERP HTTP的6项测试通过：一次成功保存/只调用一次、503失败也保存/不重试、302不跟随、动作间停止、其他路径/方法禁止，以及source→保存→validate→bulk→receipt→固定版本Consumer的双HTTP服务完整控制器组合。全部使用模拟服务与测试Key，不是真实上游验收。任务始终暂停，未创建分页/查询lease。

实际生产worker副本没有完整run builder/reader，因此在本地冻结副本明确加入六个模块：google_domain、google_serp_delivery、google_whale_readback及三个google_web_once模块。43项在此候选依赖下通过，所有realtime import均绑定候选包；外部模块借用0，非loopback连接尝试0。保护/原业务模块未替换。没有把这六模块复制到生产。

证据：`state/google-data-closure-20261106/web-once/source-verification-summary.json`、`worker-frozen-tests.json`、`worker-frozen-tests.log`。本轮测试schema已清理；专用测试PostgreSQL收尾停止。

尚缺生产独立只读idle/preflight实现、串接CLI（含准确Key输入/停止/冷却等待）、未知intent人工只读恢复与部署/运行命令。现有idle_guard注入接口不冒充这些功能已经完成；用户尚未授权新搜索/单条上传，继续不执行。

## CLI与真实只读前检增补（仍未执行真实验收）

新增 `python -m realtime.google_web_once_cli`，默认plan只输出执行清单/哈希，不连接服务、不创建目录；preflight仅数据库只读事务，advance要求显式匹配执行清单哈希。清单绑定固定查询/国家/语言/页数/次数、Dataset/source/collector、journal目录、platform origin、OpenSERP origin/proxy profile/timeout、数据库目标指纹（不含密码）及整个Python包的逐文件hash。调用方仍须先有真实用户授权，哈希本身不是批准。

IdleGuard在repeatable-read/read-only事务读取四个自动开关及手动任务、分页lease、查询lease、上传lease、Whale任务/未报告停止任务、客户任务和启用监测；缺表/未知/非零停止。动作间及STOP再次检查，不覆盖任何设置。生产数据库本轮通过同一guard只读检查：四开关false、七项计数0，未部署新模块或新建业务事实。

CLI显式保存execution绑定、preflight与控制器journal。再次advance只能延续该journal，不产生第二次搜索/写入；遇receipt冷却直接返回not-before，不在进程内长时间sleep。调用人按not-before再次运行；达到10次poll或未知source/bulk intent时停止并检查证据，不据观察超时判断失败。

最终49项在实际worker冻结依赖+7个明确新增模块中通过，包外import0、非loopback连接0。新增测试覆盖默认无连接/无文件、无授权advance不连接、执行范围绑定、真实数据库guard与完整CLI→source HTTP→保存→Writer/receipt/Consumer HTTP，重复CLI仍一次搜索/一次bulk。初次CLI组合测试因测试替身的Python方法绑定失败，修正替身绑定后通过，未放宽生产代码。

已生成本地独立sidecar包 `state/google-data-closure-20261106/web-once/worker-once-sidecar.tar`（51个源码/asset文件，含既有冻结依赖，SHA256 `2ef825d8cb7f6714cb03d029f977e580f069584ae8bfaeb59bf32fca27b1082f`）和逐文件manifest。它只准备放入独立state子目录，不覆盖 `/app/realtime`，不重启worker、不修改Compose。目前尚未暂存到服务器。

实际执行形态（参数须由下一阶段只读获取，当前不是执行授权）：

```text
python -m realtime.google_web_once_cli plan --journal <独立私有目录> --dataset-id <既有Dataset> --source-name <既有source> --collector-id <既有collector> --platform-origin <既有HTTPS origin>
python -m realtime.google_web_once_cli preflight <同一组参数>
# 只有用户明确批准后：
python -m realtime.google_web_once_cli advance <同一组参数> --authorized-manifest-hash <上述实际清单哈希>
```

Writer使用既有config，Consumer从SEARCHFLOW_ONCE_CONSUMER_KEY读取；不在argv或日志打印，不新建Key/改权限。真实worker尚未具备此Consumer运行输入，下一步将sidecar暂存、仅执行plan/preflight得到可审阅的实际清单，确认凭据最小临时传递和停止位置，再请求明确一次搜索/一次保存快照上传授权。未知intent的人工只读对账/恢复工具仍未补齐，不能把CLI可运行当作整套可靠性验收完成。

证据：`cli-verification-summary.json`、`worker-cli-frozen-tests.json/log`、`cli-production-readonly-preflight.json`、`worker-once-sidecar-manifest.json`。本机测试schema0残留，专用PostgreSQL已停止。本轮没有真实新搜索、平台写入、来源许可或正式ReaderAdmission变化。

实际sidecar已独立暂存，运行目录未替换、容器未重启；plan/preflight通过，执行清单固定。此前“未暂存”描述为历史阶段。现可提交明确的一次Web/一次保存快照单消息bulk技术验收授权，详见 `docs/google-web-once-execution-review-2026-11-06.md`。本轮仍未advance、新搜索或平台写入。
