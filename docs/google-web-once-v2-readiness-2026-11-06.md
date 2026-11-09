# 单次Web技术验收v2：独立候选更新（2026-11-06）

v2已独立暂存，但执行仍禁用。没有真实搜索/上传/平台读取，未读或传递Consumer Key；没有覆盖运行包、重启容器或更改业务数据/开关。

## 与v1的准确差异

以原51文件独立包为基线，仅将free_google.py、serp_fields.py替换为已在常驻worker上线的occurrence修复精确版本。这样单次工具不再绕过最新修复。其余7个工具模块沿用先前验证版本；没有混入原始字节加密模块、raw sink、生产key或新依赖。

v2路径：采集机 `/home/azureuser/projects/realtime-web-search-openserp/state/web-once-20261106-v2`，容器内 `/app/state/web-once-20261106-v2`，私有源码600/目录700。旧v1保留为历史准备证据，运行入口不再指向它。

运行包SHA256 `27d1373cec461adcd3248ade49a39edf8f855bc888522cf1850338bdb05a045a`，51文件逐一核对；非新增工具文件还与当前实际生产worker逐一匹配。

实际plan与只读preflight均exit0，run目录未创建。新执行清单hash：

`04f34264d025ce5bc6c7694c37781d5c7390a12d18652d4addb2ab767a36a01d`

准确绑定的现有Dataset/source/collector、OpenSERP origin/profile/timeout、数据库目标指纹、代码hash和独立journal见 `state/google-data-closure-20261106/web-once-v2/staging/actual-plan-preflight.json`。本次只是旧技术验收范围的可执行候选更新，不是正式来源/用途或ReaderAdmission批准。

## 测试与真实状态

77项隔离测试最终通过、0失败/跳过、0包外realtime module、0非loopback连接。初次业务77项通过但守卫发现借用工作树serp_presentation；保留失败记录，随后从已知Web冻结副本显式绑定为test-only support重测通过。此模块不进入51文件运行包，不能把测试支持模块说成worker运行依赖。

有1项明确不适用的opt-in raw-capture测试被列出并排除：该功能不是此v2包的内容。其余单次source/DB/HTTP/CLI/Consumer/重复URL及字段测试均执行；不是为了避开运行功能失败而跳过测试。加密raw能力的127项是独立候选证据，不能混用。

启动器已指向v2，其4项边界测试通过。真实受限SSH preflight再次返回四开关false、七项在途/监测0。暂存与plan/preflight前后两机运行源码、7个相关容器ID/启动时间/镜像/挂载内容及所有业务快照字段一致；请求39840、pages332768、旧outbox9queued未变。

本机测试schema0，专用PostgreSQL已停止。未启动本机Docker，无新增浏览器资源。

## 执行仍不允许

`scripts/google_web_once_remote.py` 的EXECUTION_REVIEW_SUSPENDED仍true，即使提供新清单hash也不会读取Key、连接执行或调用advance。待明确的新搜索/上传授权仍未得到回答；平台最近实测402/credit_insufficient尚未恢复，本轮没有反复调用平台探测或充值。

后续若获得明确批准且既有Consumer读取恢复，受限启动器仍须先核对已保存完整样本固定版本可读，再进入同journal的一次source/单消息bulk；失败/queued/未知不产生第二次写入。不能直接调用底层CLI绕过审核入口。旧v1 hash/命令已废弃，不对当前v2构成批准。

本次候选保存/交付的是完整**已存公开快照与过滤JSON投影**，不是私有HTTP字节留存、出版者正文、正式schema、真实1000样本/七天/百万行。没有因为候选更新而缩减原总目标或把这些条件标为完成。

证据：本地 `state/google-data-closure-20261106/web-once-v2/verification-summary.json`，frozen-tests及初次记录、validation-support、sidecar-manifest、staging/实际plan与前后快照。
