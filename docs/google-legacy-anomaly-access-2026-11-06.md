# 旧摘要异常：历史快照接口与当前读取阻塞（2026-11-06）

本轮继续只读对账，没有新搜索、上传、重写旧queued或修改账号/权限/额度。此前单次新运行授权问题尚未获得回答。

## 当前新增事实

本地已保存平台OpenAPI有 `/v1/documents/{id}/snapshots`，字段包括Dataset/platform/source_name/record_key、payload_hash、version_id和content_hash；这比仅用内容版本raw hash更适合查原事件。但现有Consumer实际读取此文档snapshots未成功：SDK先报告通用source_error；一次独立HTTP诊断确认402（该回包顶层code未被保存，不补猜）。随后普通document GET同样402，顶层错误码明确为 **credit_insufficient**，没有Retry-After。

三次均为已有固定对象的只读API请求，无自动重试/翻页/换Key、无充值/套餐修改。当前不能继续真实Consumer对账；不能沿用早先200证明当前仍可读。402本身不证明旧文档丢失、不证明数据损坏，也不提供历史差异根因。

精确对象：`doc_ecfebc6817d2a9bb3de07d049a76a949` / `ver_59cb5c941fd025cb9cf99594b489206e`。旧证据的本地事件hash `805037a4462eb5b9680395c162b9d288080fd91de14b97d91e09058f1b23c4d4`；该固定版本raw hash `3518cae8d234ae44710a6d3a8138d827e6e0ff052de49ae64fbffc886ae9df68`。旧只读审计已证明本地事件可复算、当时当前metadata匹配且固定版本没有完整platform_payload；本轮没有拿到新的payload，保留原结论不覆盖。

平台OpenAPI对source.payload_hash与version.raw_payload_hash仅给出字符串类型，没有说明两者必须同语义。可能存在入站事件与内容版本去重关系，但这只是待查假设，不能据8个其他样本相等而认定本条必定应相等，亦不能借假设豁免本条交付对账。

## 平台侧待查清单（仅草稿，未发送）

- 现有Consumer在2026-11-06 UTC08:36～08:37对该对象返回402/credit_insufficient。请查现有应用的读取计费/额度状态，以及恢复只读访问的准确条件；不由代理自动采购或改配额/权限。
- 按Dataset `social_media_raw`、platform `google_search`、source `realtime-web-search.serp`、完整record_key查询该历史事件：`google_search:serp:1af9c59bab305ac4f1e4106e7f144de208beed412dc82d1f11f5d827b5d5a49d:805037a4462eb5b9680395c162b9d288080fd91de14b97d91e09058f1b23c4d4`。
- 返回该事件入站载荷摘要/必要原字段、幂等判定、writer终态和document/version绑定；确认是否复用了更早内容版本，metadata更新是否独立于content版本。
- 说明该时期raw_payload_hash算法/输入与schema/服务版本；如有snapshot，提供同事件payload_hash、version_id/content_hash关联。不能只用当前metadata或当前版本替代历史入站证明。
- 保留原事件、版本及异常；不通过重新上传“修复”已有证据。

## 对待批准验收的影响

受限SSH启动器新增Consumer读前检：只有一次搜索/上传获明确授权后，先验证已有完整样本的文档与固定版本当前可读；任何失败在进入mutating CLI前停止，不先搜索/上传再发现无法验收。现有3项启动器边界测试通过，新增测试确认读取失败不会调用CLI。该gate仅改本机启动器，没有改staged包、执行清单hash或生产服务；仍必须走审核中的受限启动器，不能绕过直接调用底层advance。

当前新验收须同时具备原明确运行授权与已恢复的Consumer读取能力。agent不自行充值，不把此次自动goal续行视为批准。CLI数据库前检的此前通过只证明四开关/在途状态，不能证明平台读取正常。

证据：`state/google-data-closure-20261106/legacy-snapshot-audit/consumer-history.json`、`snapshot-http-diagnostic.json`、`document-http-diagnostic.json`及`verification-summary.json`。历史证据 `legacy-local-audit.json`、`legacy-version-audit.json`保持原样。
