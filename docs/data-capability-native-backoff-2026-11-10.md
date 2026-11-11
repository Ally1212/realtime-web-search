# SerpApi分阶段有界重试（本地第十八阶段，2026-11-10）

本阶段把第十七阶段的可信策略扩展到Trends、AIO和AI Mode候选，补齐需求第10节的对应本地调用链。默认仍禁用来源、无额外重试许可，不读取凭据、不调用供应商、不挂载生产。Google Ads路径保持第十七阶段实现。

## 任务级策略和计费

复用 `RetryPolicy` 的四字段；可信操作员同时配置适配器和租户grant，策略冻结到任务admission，客户不可写。多次调用未批准或策略不一致时执行器不就绪；显式策略必须有原文保留与单次费用上限，不能202后才发现无可执行条件。

新增内部 `SourceRetrySession`，单个任务共享：

- 总调用数，1–8，包含成功调用、失败调用和AIO正常搜索/展开；不同阶段不分别重置次数。
- 从任务执行开始的总墙钟/单调时钟窗口；后续阶段不重置。每次transport期限在共享窗口、lease及原生阶段期限内。
- 已确认实际费用累计、单次上界和原任务预留；调用前重新检查权限、取消、原文仓、范围与剩余费用。预算不会因分阶段或重试扩大。

只有完整HTTP429/503错误响应可成为继续调用的依据：非空字符串error，顶层不含成功结果，metadata若存在必须明确Error；参数echo若出现必须符合当前完整条件。Processing、未知状态、Success与error冲突、错误echo、混合结果、部分回答、缺点/缺月份、解析失败、输运/费用未知均不重发。错误响应无echo时只按固定TLS精确请求绑定错误证据，不将其当已验证成功事实。

独立账单确认和许可内原响应保存先于下一次调用。每次调用使用不同phase并保留账单，未知后次费用保留先前费用检查点与全部预留，进入reconciliation_required并阻断scope；不把部分已知合计当最终费用。确定费用后若下一次门禁拒绝，按已发生费用结算，不能归0或误报未知费用。

延迟遵守Retry-After与有上限指数退避的较大值；达到次数/预算/窗口上限后仍保存共享冷却。成功阶段会重置连续失败的退避级别，但不重置整个任务的次数、时间窗口或累计费用。取消、停止或门禁变化后不进入下一次来源调用。

## 三条来源路径

### Trends / AI Mode

原有单次路径保持兼容。仅显式获准策略进入逐次门禁和journal，使用 `query`、`query_retry_2` 等phase。最后成功响应沿用对应原生标准化器；前次错误保留在来源journal，不混入数值点、回答或引用。

每次调用都有独立账单和response checkpoint，最终费用为所有确认账单之和。原生成功但partial、不支持字段或解析失败不会引起自动重发，也不以其他AI通道替代。

### AIO

使用原生 `search` → 可选 `expand`，分别处理失败阶段：

| 已有结果与失败位置 | 允许的行为 |
|---|---|
| 搜索临时整批错误 | 仅重试搜索，phase为search_retry_N |
| 搜索成功、展开临时错误 | 仅重试同一展开token，phase为expand_retry_N；不重发成功搜索 |
| 已有部分回答/未出现回答/拒答样式/数据不完整 | 保留真实状态，不据此重试成功阶段 |
| token时间不足或已过期、费用/次数/全局窗口不足 | 保留已保存原响应；已有inline回答可作为partial事实返回 |
| 展开后次费用未知 | 不再调用，保留先前响应与账单并隔离，不能按部分合计结算 |

原生token截止仍取成功搜索的创建时间加59秒与任务期限的更早值；策略不能延长token生命周期。初始失败搜索的重试不占用一个尚未取得的token窗口，取得token后仍受同一个任务总窗口限制。两阶段共享最多8次门禁和16MiB等既有响应仓约束。

短效token仅在进程内继续请求，不进入phase、账单ref或原文/导出；原文脱敏仍移除token及其他字段中的重复敏感值。供应商账单回调沿用search/expand阶段类型，持久账单使用唯一实际phase。

## 客户合同与兼容

OpenAPI更新为1.9.0，catalog声明三通道有界重试。preview返回拟用策略，job/progress显示冻结策略，逐次phase、费用和来源journal继续可查。null表示没有额外自动重试许可；它不取消AIO原有已批准的正常搜索+展开。

独立事实、JSON/JSONL/CSV/ZIP/manifest、子记录和订阅保持原合同。来源journal固定版本ZIP包含全部尝试并可独立校验；事实只描述最后有效来源结果，不把错误响应冒充新观测。旧成功事实与父任务费用不覆盖。

只读例子：

```bash
curl --fail-with-body "$BASE/v1/jobs/$JOB_ID/progress" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY"
curl --fail-with-body "$BASE/v1/jobs/$JOB_ID/response/export?format=zip" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY" --output source-calls.zip
```

## 验证与未完成

隔离验证使用合成原生响应、实际候选执行器/数据库预留/客户HTTP/来源journal：三个通道的503/429重试、不同phase与账单总计、对应事实、来源与事实固定ZIP独立校验、token/凭据不泄露、错误/Processing/矛盾echo/混合数据不重发、取消、未知后次费用隔离、次数/累计预算与终止后冷却；AIO两条失败阶段分别验证，另覆盖成功搜索不重复、token不足、partial不重发、总次数阻止展开和首次搜索重试门禁变化仍结算前次费用。最终计数、源码摘要和清理见 `state/data-capabilities-20261110/native-backoff-manifest.json`。

未做真实供应商失败、真实许可/Key/预算/独立账单、100条series/100关键词/500回答或双市场/设备验收。未部署、未改生产开关、未运行旧迁移或提交Git，保护文件未覆盖。本阶段不提供死进程等待状态的自动续试，也未完成失败/取消子任务续试、部分月份最小范围、完整清除/备份恢复、真实Whale各域读回、规模与七天验收；整个需求目标仍未完成。

最终隔离回归1358 passed / 698 subtests / 0 skipped（186.99秒）；专项145 passed / 263 subtests（58.59秒，与全量重叠）。598个OpenAPI本地引用和两层鉴权示例bash语法通过。均为合成来源与账单，不算真实金标；两套本轮PostgreSQL/Redis及测试HTTP/worker均停止、端口关闭，自己的空socket与合成PG数据目录已清理。没有新浏览器资源、生产部署、真实调用/收费、旧迁移或Git提交；web.py/serp_page.py保持原保护摘要。源码冻结、测试及清理记录见state/data-capabilities-20261110/native-backoff-manifest.json。
