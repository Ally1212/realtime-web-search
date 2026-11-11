# 原生来源绑定与审批装配（本地第二十七阶段）

新增 `NativeSourceRoute` / `NativeSources`，为现有四个原生候选适配器接入显式部署组件：SerpApi Trends、SerpApi AIO、SerpApi AI Mode、Google Ads搜索量。没有新增来源采购、凭据发现、OAuth授权、来源调用或正式挂载。

每个route明确adapter、license_id、共享账号scope、币种、间隔和lease；capability/provider由准确的原生适配器类型决定，不从客户请求选择执行代码。每能力只配置一个route，不自动fallback。所有同一供应商账号/资产应使用正确共享scope，系统无法从任意scope字符串判断实际账号关系。拒绝禁用适配器、Ads资产币种错配和账单回调参数错配：Trends/AI Mode为(status,payload)，AIO为(phase,status,payload)，Ads为(status,payload,request_id)。只检查签名，不执行账单查询或假定免费。

可信approval_for(tenant,capability)每次返回以下JSON记录或None，不缓存授权、不接收客户提交的授权。超过32KiB、额外字段、可执行validator、来源/许可/币种/计费单位/租户错配、撤权/到期、未来核验时间、原文许可缺失或重试策略不一致均拒绝。Ads用途还须与已核验资产scope一致。原生adapter自身继续核验真实支持参数，不能用通用validator跳过地区/语言/月份/通道限制。

```json
{
  "tenant_id": "operator-approved-tenant",
  "evidence_id": "00000000-0000-4000-8000-000000000001",
  "verified_at": "2026-11-10T00:00:00Z",
  "grant": {
    "capability": "trends_history",
    "status": "approved",
    "provider": "serpapi",
    "license_id": "operator-approved-license",
    "allowed_use": "approved-use",
    "retention_days": 1,
    "billing_unit": "operator-confirmed-unit",
    "currency": "USD",
    "maximum_cost": "0.40",
    "expires_at": "2026-11-11T00:00:00Z",
    "raw_response_retention_allowed": true
  }
}
```

以上是格式示例，不是许可、真实报价或可投入生产的授权。evidence_id必须是独立审批记录的规范UUID。此组件仅校验可信记录及绑定一致性，不连接外部许可系统验证审批真实性。密钥、短效token、OAuth资产和独立账单resolver仍由批准范围内的可信启动代码传入原生adapter，不能放在审批JSON。

```python
from realtime.data_capability_native_sources import NativeSourceRoute, NativeSources

route = NativeSourceRoute(explicitly_configured_native_adapter,
    license_id=approved_license_id, scope=approved_shared_account_scope,
    currency=approved_currency, interval_seconds=approved_interval)
sources = NativeSources([route], current_operator_approval_loader)
# factory只构造对象；预算配置、初始化、恢复、启动、挂载均需显式调用。
app = sources.deployment(data, cursor_secret=private_cursor_secret,
    checkpoint_id=independently_retained_latest_v3_id,
    checkpoint_hash=independently_retained_latest_v3_sha256)
app.initialize()
app.recover_file(private_checkpoint_path, independent_signing_key)
app.start()
app.mount(existing_authenticated_server)
```

factory统一当前授权解析器、原生validator、执行器、独立响应仓与API运行时的对象身份，沿用有界响应仓默认值或明确response_policy。不会创建预算、抬高额度或授权重试。审批引用 `admission_evidence_ref=approval:<UUID>` 和 `admission_verified_at` 进入任务及观测内部admission快照；公共合同校验它们成对出现、UUID规范、时间非未来。只保存引用与时间，不保存完整审批文件、凭据或执行代码，不更改既有事实hash/ID。

验证使用供应商格式的合成响应，经过真实本地双层鉴权HTTP、原生参数转换/标准化、响应仓、费用与ZIP导出。四域共有5次合成transport调用（AIO搜索+展开各一次），逐任务保留幂等键/审批引用和0.1/0.2合成账单；撤权后详情/导出拒绝且不增加调用。没有真实来源请求或真实费用，不能将其称作真实金标或采购验收。

仍缺真实许可/账号/Key/预算/独立费用及原生样本金标、正式serve/systemd接入、Whale逐域schema与固定版本读回、独立检查点自动保管、完整撤权清理/严格发送屏障、规模和七天。现有生产开关及历史数据保持。主OpenAPI1.9.0/capabilities/fields同步说明来源绑定与审批引用，无客户审批/密钥配置端点。

最终证据：`state/data-capabilities-20261110/native-bindings-manifest.json`。

第二十七阶段最终隔离回归 **1432 passed / 774 subtests / 0 skipped / 0 failed**（210.59秒）；专项 **172 passed / 175 subtests**（32.67秒，与全量重叠）。618个OpenAPI引用、原生绑定/审批引用/双层鉴权合同通过。四套本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；web.py/serp_page.py保护摘要不变。没有生产部署/恢复/删除、真实请求/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/native-bindings-manifest.json`；真实接通和全需求仍未完成。
