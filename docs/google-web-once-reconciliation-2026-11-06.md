# 单次验收未知状态：只读对账（2026-11-06）

本轮未收到上一轮新搜索/上传授权，因此没有advance、部署或真实Google/Whale请求。继续实现不依赖该授权的恢复准备：`realtime/google_web_once_reconcile.inspect_journal`。这是只读Python接口，还未加入已暂存sidecar，不改变待批准执行清单。

## 行为与证据界限

输入既有私有journal、准确execution清单hash与只读reader；先核对目录/文件、execution/plan绑定与源请求身份、时间和查询条件。source_intent缺少证据只返回outcome_unknown；有唯一保存ID时可只读验证该结果。失败源返回saved_source_nonresults，不创建新搜索或把它改成成功。

bulk_intent、awaiting_terminal、terminal或verified在保存message hash一致后，按Dataset/platform/source_name/record_key只读查询一次receipt，已有ack/target的document/version不得改变。仅accepted/duplicate再做独立Consumer固定版本完整payload核对。结果即使能证明确已持久化/读回，也不修改旧intent、不授权重发、不自动接续写操作。旧冷却未到期则不查询receipt；unknown/queued/retrying不是失败，failed/rejected也不授权重发。

接口不创建目录/lock、不写计数/状态/报告、不调用source/validate/bulk；报告仅返回内存中的诊断摘要。若调用人要保存报告，须选择独立证据文件，不覆盖journal。本次9项新增测试逐文件比较前后hash及文件集合，验证上述不变性；连同既有控制器/本机HTTP/reader/builder共46项通过、0失败/跳过。所有source/platform均为替身或loopback服务，不是真实中断恢复验收。

输入symlink、错误manifest、错误事件、改变的源载荷或错误固定payload会拒绝；任何缺证据情况不据此推定上游失败。读取异常由调用者处理，不能自动重发。

## 使用接口

```python
report = inspect_journal(
    existing_private_journal,
    expected_manifest_hash=reviewed_execution_hash,
    reader=read_only_saved_source_receipt_and_consumer_adapter,
)
assert report['journal_changed'] is False
assert report['replay_authorized'] is False
```

这里的reader须使用已有SQL只读public保存记录与精确receipt/Consumer读取方法；接口不会替调用者授予外部权限。生产CLI/人工诊断入口和同一真实journal恢复仍待后续独立接线/授权，不以库测试冒充完成。

当前实际待批准包仍为 `web-once-20261106-v1`，执行hash `dbbc29288c5d2d7a0c0902f126306abb14645583a0c9978c18bc3a9717b9bd06`，准确一次范围见 `docs/google-web-once-execution-review-2026-11-06.md`。未将自动goal续行视为用户对该问题的同意。

对应GO-ID-003、GO-ING-001/003/005及WP-GO-01/02仅增加恢复准备的局部证据；正式准入、旧摘要异常、News/Trends真实交付、七天与真实百万行仍未完成。证据 `state/google-data-closure-20261106/web-once/reconciliation-verification-summary.json`。
