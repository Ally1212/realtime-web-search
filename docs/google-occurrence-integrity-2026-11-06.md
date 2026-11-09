# Web保存完整性审计：同响应重复URL与上游JSON（2026-11-06）

本轮没有真实搜索/上传、没有修改生产服务、没有恢复任何自动开关。平台402/credit_insufficient与真实运行授权问题保持原状态。

## 已确认缺口

现有OpenSERP transport先以URL字典合并结果，再把唯一SearchResult列表传给response_envelope。即使SerpStore和标准化模型能保留重复URL occurrence，经过真实transport的同响应重复出现已经丢失；不能用直接给Store喂重复项的旧测试代替整段采集路径。

成功响应只重建upstream_query/upstream_metadata、选定模块和过滤结果，并非完整OpenSERP JSON；失败或无效JSON路径多数只保留错误分类/hash等证据。此前固定版本与“已保存源响应”匹配，只证明上传了当时保存的重建响应，不证明当时HTTP所有原字段/字节完整保存。历史缺失不能由后续匹配或生成空值补回。

## 本地修复

- transport保留有序organic_observations列表，保存每次有效自然结果的标题/摘要/原rank。仅下游SearchResult工作列表继续按URL去重，不改变每URL一次后续工作；数据库URL registry仍全历史去重。
- 成功envelope增加upstream_response=public_fields(payload)，保留上游JSON的结果顺序、重复项和未知公开字段。upstream_response_capture明确representation=filtered_parsed_json、field_filter=public_fields、verbatim_http_body_retained=false；不能把它叫原始HTTP字节或声称过滤前JSON逐字可复现。
- 失败body、非JSON、原始字节安全留存/期限/权限隔离尚未实现；本次没有把未经隔离的原字节或可能含凭据的错误正文塞进公开结果/导出。

仅修改本地free_google.py/serp_fields.py及测试。另从实际worker冻结依赖制作仅两文件的最小候选，未将包含其他既有差异的工作树整文件覆盖到生产。未修改运行中的web.py/serp_page.py，也未修改已暂存v1包。

## 验证

真实隔离PostgreSQL+loopback OpenSERP测试用同URL两条不同标题结果穿过transport→保存→normalize：2个独立occurrence、1个URL identity、1个首次贡献，原顺序和上游结果数组保留。transport下游工作列表仍为1项。另验证未知公开字段保留、凭据键过滤、旧JSONL/CSV校验和查询/页数/失败路径。

本地相关45项通过；更广的冻结依赖116项最终通过、0失败/跳过、0包外模块、0外部socket连接尝试。首次116项业务测试虽通过，包绑定守卫发现借用了工作树google_data_export/google_legacy/serp_presentation；保留初次失败，不冒充冻结通过。最终明确从既有Web冻结副本加入三模块作验证支持（不是worker发布文件），逐文件绑定见verification-support-manifest.json后重测通过。

初次新transport fixture未关闭DNS目标验证，结果被过滤；改用与线上链接模式相同的validate_target_dns=false后验证。另一次测试命令引用不存在的test_serp_fields，已改为实际test_field_expansion。这些失败均未放宽生产目标校验或修改样本事实。

本机全部测试schema已清理，专用PostgreSQL55436已停止。证据：`state/google-data-closure-20261106/occurrence-fix/verification-summary.json`、`frozen-tests.json/log`、初次`frozen-tests-initial.*`、候选两文件before/after manifest及diff。

## 对下一步和旧结论的影响

GO-DAT-002、GO-ID-002、GO-DAT-010及核心保存要求仍部分完成，不能因116测试通过标全量验收。尤其原始HTTP字节/失败响应、真实200套多block样本、正式许可与ReaderAdmission未完成。

本机受限启动器已暂停v1 advance，即使传入旧清单hash也会拒绝；新增边界测试与已有启动器测试共4项通过。旧sidecar保留为此前准备证据，不用于真实新验收。下一步应先完成失败/原始响应的安全留存及范围定义、最小候选复核，随后更新可执行候选/清单；同时还需要现有Consumer额度恢复和原明确运行授权。不能恢复旧v1命令绕过已发现缺口，也不能把历史丢失字段标成完整。

后续两文件occurrence候选已精确上线，仅重启local-worker，业务与四开关不变。raw加密模块未部署/未启用、旧v1仍暂停；详见google-occurrence-release-2026-11-06.md。
