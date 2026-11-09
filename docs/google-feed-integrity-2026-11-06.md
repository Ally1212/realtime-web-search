# News/Trending Now保存路径审计与本地修复（2026-11-06）

本轮没有访问真实RSS/出版社、平台或新搜索，没有部署/迁移/改生产开关；Consumer额度与明确运行授权仍未解决。

## 实际发现

有效News/Trends列表原本已按顺序保存在数据库，collect_sources的URL去重发生在保存之后，不应把Web的valid重复URL丢失结论直接套到Feed。实际缺口是article()对缺失/无效URL返回None：原条目被静默跳过，Trends过滤后的child索引也压缩，News全部无效时被当成empty成功。原RSS XML字节没有留存，不能从旧保存记录补回已跳过条目。

## 本地改动

- parse_feed新增版本标记google-feed-observations-1、RSS item数与feed_parse_issues。无效News/Trends报道保留原RSS条目/子条目位置及已读取字段（标题、链接、publisher/URL、原日期、snippet、guid等），通过既有public_fields过滤凭据；不访问链接。
- 新Trends有效报道带source_child_ordinal；normalize仅对上述新版本标记使用原feed_position/child位置，并把解析问题放入invalid_records。旧无标记历史记录继续原身份/编号规则，不迁移或重新分配ID。
- News没有有效结果但存在解析问题时，collect记录parse_error/unusable_feed_items，缓存失败120秒并释放query lease；不标成“无新闻”成功。部分有效列表保留有效结果和全部已记录问题；Trends仍可保留无有效报道的话题，不冒充数值曲线。
- 公共旧数据读取/导出及既有分析filter的classification允许parse_error/http_error/rate_limited/transport_error/captcha等实际已存在状态，避免失败记录虽保存却无法按自身状态筛选。仍是参数化SQL和原public/private隔离，无新公开路由或权限变更。

## 验证范围

57项本地隔离测试通过，0失败/跳过，覆盖News无效前置条目、Trends无效报道后重复URL位置、全无效News不作成功空结果、旧标记缺失时编号不变、缓存与lease、数据库保存、Feed候选重建及私有隔离/JSONL/CSV。

新增实际隔离PostgreSQL链路用mock RSS把全无效请求保存为parse_error，invalid_records标题/位置等字段仍在；同source+classification=parse_error的公共读取函数与JSONL/CSV导出一致，manifest计invalid_records1、verify_bundle通过、query lease0。这里的API证明仅覆盖HTTP路由使用的底层读取函数，不是正式HTTPS新样本验收。

首轮无数据库环境的纯测试有2项按条件跳过，不用该结果作最终验收。新增导出测试初次缺serp_task_options表，已在其独立测试schema初始化Console，不改生产schema；最终上述57项0跳过。本机所有任务schema0残留，专用PostgreSQL55436已停止。

## 仍不成立的声明

保留的是既有解析器已读取的字段与问题，不是完整RSS XML/HTTP实体留存，也不保证所有未知RSS元素已保存。News包装链接仍可能未解析；日期依然只按真实RSS pubDate依据，不等于出版者网页验证；Trends不是数值历史序列。

改动仅本地，尚未形成通过的生产冻结候选。google_data.py在Web、worker、工作树有既有差异，后续必须只叠加这三个新分支，不能顺带覆盖Images分支或其他历史改动；需要worker与Web读取/导出语义配套。原冻结Web/worker以及v2单次Web工具均未被本轮改写。

正式来源/用途/schema/ReaderAdmission、各来源真实终态和固定版本、七天及真实百万行仍待完成。对应GO-SRC-007、GO-DAT-002/010、GO-ID-002/006、GO-ING-001、GO-POC-05只增加局部代码/隔离证据，不提升整项为已验收。下一步是最小冻结候选验证，不能为验收开启Feed采集或平台上传。

证据 `state/google-data-closure-20261106/feed-integrity/verification-summary.json`，相关源/测试hash绑定当前修改。

后续最小冻结候选已完成：worker48/Web48分别通过，五文件仅本地包，保留两机旧差异，before hash真实只读匹配；未部署。详见google-feed-frozen-review-2026-11-06.md。

后续五文件冻结候选已上线，先Web后worker，只重启web/prd/local-worker；源请求/旧队列/四开关保持不变。原生模拟及HTTPS5项通过，未真实采集或平台调用。详见google-feed-integrity-release-2026-11-06.md。
