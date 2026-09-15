# Google 非主流采集方法与产能评估

## 结论

目前最值得投入的路线是：保留已经能返回搜索结果的 Nokia/WML 通道，修复搜索调度损耗、提高每次搜索带来的新增正文数量，再比较有限的设备配置、稳定会话和出口组合。没有公开证据足以证明某个免费入口可以长期、无人值守、无限量地获取 Google 数据。

当前项目已经实现 `curl_cffi + Nokia6230 User-Agent + /wml/search`。这与 SearXNG、DonSeTch 社区近期探索的方向相近，不能再次包装成尚未落地的新优化。新的机会主要在配置选择、搜索供给、低产查询淘汰和正文有效率，浏览器作为独立备选通道。Google News RSS 和 CSE 可以单独研究，但不能直接混入当前 Google Web 实验。

多种网上流传的“隐藏接口”已有明确失效记录：Google Go 内部 JSON 搜索、Google Docs Explore 搜索和 Whoogle 的原有无 JavaScript 路线都不适合作为新的生产基础。`num=100`、切换国家域名和随机指纹也不能提供可靠的十倍产能。评估的核心应从“返回 HTTP 200”转为“每千次请求最终产生多少条去重、相关、正文有效且被 Whale 新增接收的数据”。

## 范围与证据标准

证据截至 2026 年 9 月 15 日。考察对象为公开搜索结果、开源项目、维护者讨论、原始测试数据和官方接口说明。范围限定在通过 Google 发现 URL；文章正文仍需访问原始网站获取。搜索摘要、关键词建议、跳转链接和接口成功次数不能算作文章。

证据分为四类：**官方说明**能证明产品约束；**源码或合并记录**能证明实现存在；**作者自报**只能证明某一环境中的观察；**本项目实测**才可用于本项目容量判断。源码存在不等于接口今天可用，测试通过反爬判定不等于取得有效搜索链接，短时成功也不等于日增量稳定。

当前验收要求是每天 100,000 条新增接收，即每小时约 4,167 条、每分钟约 69.44 条，完整两小时参照线为 8,334 条。正文必须满足现有相关性、长度、质量和历史去重规则；缺少符合要求的发布时间仍会阻止上传。Whale 客户端的 accepted/queued 回执表明上传端接收，不额外证明后续已经完成索引并可检索。

实验中的 `zh` 限定查询语言；现有正文质量规则允许中英文，并以 AI 语境、正文长度及页面质量规则判断相关性。因此统计不能称为“全部为中文正文”或“逐条人工确认相关”。历史去重使用规范化 URL 与正文 SHA-256，尚不等于语义层面的近重复去重。

## 方法总览

| 方法 | 所在环节 | 当前证据与限制 | 项目判断 |
| --- | --- | --- | --- |
| Nokia/WML 轻量结果页 | Google Web 发现 | SearXNG 已合并；同时存在被封反馈 | **已使用，继续作为主通道评估** |
| 多个已验证的 Nokia 固件配置 | Google Web 发现 | DonSeTch 选出 7 个，作者明确承认间歇失败 | 下一轮小样本对照候选 |
| 匹配 TLS/HTTP 指纹、复用会话 | 传输 | curl_cffi 有实现；不执行 JavaScript | 保留，按组合统计，避免随机乱配 |
| nodriver / 直接 CDP 常驻浏览器 | Google Web 发现 | 有开源实现及有限第三方测试 | 独立备选，需要实际 SERP 验收 |
| Patchright / Camoufox | Google Web 发现 | 不同环境结果不同，无通用必胜者 | 与直接 CDP 同预算比较 |
| 住宅出口、稳定身份与冷却 | 传输与调度 | 样本依赖明显，住宅不等于稳定成功 | 优先分析已有出口；不按标签采购 |
| Google CSE Element JSONP | Google 定制搜索 | SearXNG 有适配器；新引擎收窄至站点搜索 | 独立来源候选，不替代全网 Google Web |
| Google Custom Search JSON API | Google 定制搜索 | 已关闭新客户，老客户需在 2027-01-01 前迁移 | 不作为新项目长期底座 |
| Google News RSS 与 URL decoder | Google 新闻发现 | 开源可用；解析新跳转链接需额外请求 | 新闻补充候选，须独立验收 |
| Google Go 内部 JSON 接口 | 历史隐藏入口 | 2023 年已有明确停用反馈 | 淘汰 |
| Google Docs Explore 搜索 | 历史隐藏入口 | 2026 年 7 月披露、8 月已有失效反馈 | 淘汰 |
| Whoogle 自托管 | 历史元搜索实现 | README 于 2026-07-24 宣布结束 | 不新部署 |
| `udm=14` / `num=100` / 国家域名 | 搜索参数 | 分别涉及结果过滤、失效分页参数、域名归并 | 不能当免验证或独立配额 |
| `site:`、时间片、实体、排除词 | 查询供给 | 官方支持部分运算符；产出依赖主题与语料 | **最适配当前项目，已部分实施** |
| Autocomplete、Jina Reader、元搜索 | 关键词或正文辅助 | 建议词不等于 URL；聚合结果不保证 Google 来源 | 可辅助，不能虚增 Google 发现量 |
| 商业 SERP 服务 | 外包发现层 | 需核实每次计费、返回条数、重试与来源 | 成本备选，不能从“搜索次数”推文章产能 |

## 1. Nokia/WML：当前最直接的非主流路线

SearXNG PR #6546 于 2026 年 8 月 18 日提出、8 月 22 日合并，使用旧 Nokia 设备 User-Agent 获取不依赖 JavaScript 的 Google 搜索结果。作者自报其实例在 12 小时内完成 36,000 次请求、成功率 100%。这是值得关注的实践记录，但它统计的是该实例的请求成功，未公开本项目所需的中文相关正文、历史去重和 Whale 接收漏斗。[^1]

反证很快出现。8 月 23 日的 issue #6570 报告 Nokia User-Agent 再次失效；后续评论指出在特定 `curl_cffi` 配置下仍能返回结果。SearXNG 的更早讨论也记录了 GSA iPhone/NSTNWV 等方案的失效与替代过程。合理结论是可用性依赖请求实现和环境，不能把单个“全封了”或“100% 成功”的帖子推广为全球规律。[^2][^3]

本项目的 `realtime/free_google.py` 已使用 Nokia6230、`/wml/search` 和 `chrome99_android` impersonation。正式两小时实验继续走现有 Google 发现链路；本轮主要修改了抓取流水线、查询供给、历史去重和上传调度。WML 入口属于已有能力，不能把本轮全部提升归因于新发现的入口。

DonSeTch PR #167 在 2026 年 9 月 8 日提出、9 月 9 日合并，作者在已有候选之外测试了 114 个 Nokia 配置，选出 7 个曾返回有效结果的配置，并按出口保存配置偏好。作者同时明确说明：同一个配置可能本次成功、下次 CAPTCHA；这只是 best-effort 来源；测试中的直连比 Webshare 代理更可靠。其正式搜索还能依赖其他引擎补位，因此项目整体能给结果不能证明 Google 单独稳定。[^4]

**建议。** 下一轮仅选择少数有真实成功记录的配置，与现有配置做同查询、同分页、同出口类别的交错对照。记录返回链接数、独立新 URL、有效正文和 Whale 接收；遭遇 429、CAPTCHA 时继续使用共享冷却，不把更换配置当作无限重试理由。只有取得更多合格数据、且总请求成本下降，才值得接入主调度。

## 2. 指纹、会话与出口：有效的是组合管理

curl_cffi 能模拟部分浏览器 TLS 与 HTTP 行为，成本通常低于完整浏览器，但官方 FAQ 明确指出：JA3 与 Akamai 指纹并不覆盖所有检测因素，模拟正确也可能继续被识别；库本身不能执行 JavaScript。FAQ 还明确不建议随意生成自定义随机指纹，因为正常浏览器的指纹存在版本约束，随机组合反而容易显得异常。[^5]

因此，“随机 UA + 随机 JA3 + 每请求换 IP”缺少作为稳定生产策略的证据。更值得验证的是有限的、内部一致的配置组合：出口、浏览器版本或传输配置、语言地区、Cookie 和会话寿命。会话复用也能减少握手成本，但某个 Cookie 是否改善 Google 结果必须由对照证明，不能仅凭先访问首页这一动作推断。

V2EX 2016 年帖子曾提出“先访问 Google 首页再搜索，100% 不会 CAPTCHA”；同帖回复直接反驳，指出该顺序也会触发验证码。它说明这类经验会被传播成绝对规则，却没有稳定样本。[^6]

“住宅 IP 必过”同样不能成立。公开浏览器 benchmark 的成功发生在一个住宅网络；DonSeTch 的直连与代理比较又显示特定代理更差。出口类型只是标签，出口历史、共享程度、地区、延迟和持续使用表现都会影响结果。当前最经济的动作是对已有出口计算每千次请求的有效新增接收和冷却时间，而不是先扩大代理数量。

**建议。** 以匿名出口标识建立分组统计，保存配置版本及失败类型，不保存或展示代理凭据。区分“传输失败”“验证页”“空结果”“正常但没有新 URL”。调度优先级用近期新增接收和稳定性决定；原有全局预算、冷却和请求上限保持一致，避免局部线程数把全局请求量放大。

## 3. 浏览器备选：nodriver 值得测，但尚无规模保证

nodriver 通过直接 CDP 控制浏览器；Patchright 是围绕 Playwright 的修补实现；Camoufox 是对 Firefox 行为与指纹进行调整的实现。这些项目在控制链路、浏览器版本和资源成本上不同，不能只按“stealth”标签认定效果。[^7][^8][^9]

Ian L. Paterson 在 2026 年 5 月公布了七种实现、31 个目标、三轮共 651 条记录的比较。测试持续约五小时，使用单个住宅出口和特定机器环境。Google 一行中，nodriver、Camoufox、Cloak 和 curl_cffi 被标记为 OK，普通 Playwright、Patchright、rebrowser 被标记为 blocked。这个结果支持安排小范围实测，但不能证明某个浏览器在其他 IP、中文查询或长期连续抓取时仍领先。[^10]

更关键的限制在验收方法。公开 `_classify` 代码主要检查 HTTP 状态、验证页关键词、页面标题和正文长度，并没有验证 Google 自然结果链接数量。curl_cffi 的一份 Google 原始记录标题仅为“Google Search”，响应约 91 KB，仍被标记 OK；nodriver 返回了更大的页面及含查询词的标题，但记录本身也没有给出 SERP 链接计数。因此，这份测试证明的是其分类器没有判为阻断，不能直接证明取得了可采集的 Google 结果。[^11][^12]

中文社区也有相近实践。V2EX 2026 年 6 月 1 日发布的五引擎 MCP 使用可见 Playwright、独立 profile、搜索与正文分离，遇到 CAPTCHA 等待人工处理，默认可等待 300 秒。其“免费、没有调用限制”属于作者表述，实际机制并不能证明无人值守的日量。其支持多个搜索引擎的优势也不能自动转移到 Google-only 项目。[^13]

**建议。** 先做一个有严格总时间和资源上限的常驻浏览器备选，与现有 WML 使用相同查询集。验证自然结果中的真实外链、重复页、中文结果、发布时间可用率和最终接收。需要人工验证的请求记作自动链路未完成，不能暂停计时后再称无人值守成功。没有必要在现有 24 个正文进程旁立刻启动大量浏览器；当前正文与搜索资源应独立核算。

## 4. CSE：历史“免费 JSON 搜索”建议需要更新

Google Programmable Search Element 与 Custom Search JSON API 是两种不同接口形态。SearXNG 的 `google_cse.py` 展示了从 `cse.js` 取得短期 token，再调用 `/cse/element/v1` 解析 JSONP 的实现；当前适配器每页 20 条、最多 5 页，并缓存 token。源码采用的默认第三方 CX 不应直接成为另一个生产服务的共享依赖。[^14]

这条路线看起来轻量，但查询覆盖由 CSE 引擎配置决定，结果与桌面 Google Web 不等价。SearXNG issue #6524、#6441 还记录了质量及地区语言方面的问题，中文任务尤其需要独立抽样。[^15]

更大的限制来自官方产品变化。Google 于 2026 年 1 月 20 日宣布：新建引擎必须使用“Sites to search”，该免费功能最多指定 50 个域名；已有“Search the entire web”配置可使用至 2027 年 1 月 1 日。超过该范围的需求需要迁移到另行提供的方案。[^16]

Custom Search JSON API 官方概览也明确：已不接受新客户，已有客户需要在 2027 年 1 月 1 日前迁移；现有公开额度为每日免费 100 次，额外每 1,000 次 5 美元，每日最多 10,000 次查询。不能再建议新用户“现在申请一个免费 Google API key 就能无限采集”。[^17]

**建议。** 只有在具备适用的自有引擎和明确来源标识时才做独立评估。对于已知优质站点的覆盖，Google Web 的 `site:` 查询与站点型 CSE可以比较；不能将后者称为等价的全网搜索，更不能依赖别人的引擎 ID、临时 token 或演示站点配额解决长期容量。

## 5. Google News RSS：可能提升新闻供给，不能省掉正文链路

GNews 开源项目利用 Google News RSS 提供标题、URL 和发布时间等信息，适合事件和新闻发现。其 `max_results=100` 是对返回 feed 列表的截取上限，不代表一个关键词存在无限分页，也不保证每次都有 100 条独立新文章。不同关键词和日期片会高度重叠。[^18]

Google News 的文章 URL 还可能是封装跳转链接。`google-news-url-decoder` 的实现显示，新格式常需请求获取 signature/timestamp，再调用 Google 的内部批处理接口解析；不能把所有链接都当作离线 Base64 解码。2026 年 8 月的 PR #19 还指出超时和异步 UA 路径上的问题，说明“安装包能用”仍需配合超时、失败分类和资源回收。[^19][^20]

完整成本是 RSS 请求、URL 解析请求、原站正文请求和失败重试之和。新闻 feed 的 `pubDate` 需要保留来源语义，不能无条件写成原站首次发布时间。即便解决了日期缺失，也必须证明该日期满足 Whale 字段要求。

**建议。** Google News 是单独的 Google 产品来源，可以列入后续候选，但当前正式实验不加入。若未来采用，分别标记 `google_web` 与 `google_news_rss`，在共享 URL/正文去重之后报告 News 的独立贡献。优先评估时效性明确、正文可访问、原站日期完整的新闻站点。

## 6. 已有失效证据的“隐藏入口”

**Google Go 内部 JSON 搜索。** SearXNG issue #1642 于 2022 年讨论了 `asearch=arc` 与内部 async 参数的 JSON 输出；维护者在 2023 年 5 月 22 日明确表示 Google 已不允许该用法并关闭问题。issue 最近被更新不代表接口在 2026 年复活。[^21]

**Google Docs Explore。** 2026 年 7 月的 SearXNG 讨论披露了文档 Explore 搜索路径，可以取得每次约 20 条 JSON 结果；8 月 21 日已有失败反馈。它依赖未承诺稳定的文档功能，不适合借某个公共文档长期提供搜索服务。此处将其作为历史接口寿命的案例，不列为实施方案。[^22]

**Whoogle。** README 在 2026 年 7 月 24 日明确宣布项目走到终点，原无 JavaScript 路径及其 CSE fallback 均不再作为可工作方案；历史安装说明被保留作记录。继续推荐“一键部署 Whoogle 就能稳定 Google 搜索”会误导容量决策。这个声明描述的是 Whoogle 的可用路径，并不证明后来所有 Nokia/WML 组合都失效。[^23]

这些案例共同说明：一个内部入口今天返回 JSON，只能减少当前解析工作；它没有自动提供稳定性、分页覆盖和容量承诺。系统应能替换入口，业务账本、正文获取、质量验收和上传去重应保持独立。

## 7. 参数技巧与查询扩展

`udm=14` 用于更聚焦的 Web 搜索结果视图。它可以改变返回内容类型，但没有证据表明它提供免验证、更高配额或更多独立分页。Google 官方也将 Web 归为搜索过滤方式。[^24]

`num=100` 曾被广泛用于一页获取更多结果，但 Search Engine Roundtable 在 2025 年 9 月 12 日记录了该参数开始被忽略的现象。该证据不等于所有 Google 端点都绝对不能返回 100 条，却足以否定把“加一个参数即可十倍提速”作为当前稳定方案。每个实际端点都应以解析出的独立链接数验收。[^25]

切换 `google.co.uk`、`google.de` 等国家域名也不应当作独立配额池。Google 2025 年 4 月公告说明，国家域名逐步转向 `google.com`，地理化结果早已通过其他机制实现。域名数量不能直接乘到容量上。[^26]

真正适合当前目标的是把过宽的查询拆成有产出的子集：主题 × 实体、主题 × 站点、主题 × 年份或月份，以及适当排除高重复站点。官方支持 `site:`、排除词和日期运算符；但其对 `after:`、`before:` 的说明是按文档 last updated 过滤，不能据此填充文章首次发布时间。[^27]

查询扩展应按边际新产出调度。重复抓取“人工智能”第十页，可能不如一个有明确正文和日期结构的站点查询。需要同时保留一定探索预算，避免只采固定站点导致覆盖收窄。Autocomplete 可提供关键词建议，但返回的是建议词，不是文章 URL，不得作为发现数量。[^28]

**建议。** 当前已加入历史年份和高接收站点查询。下一轮根据“每百次请求 Whale 独立新增接收”分配查询预算，低产查询减少翻页，高产站点用月份、实体和子主题继续拆分。不能仅以 SERP 宣称的“约有多少结果”估计可访问总量。

## 8. Jina、元搜索与商业服务的边界

Jina Reader 的 `r.jina.ai` 对已知 URL 做正文转换；`s.jina.ai` 提供 Web 搜索。README 并未保证每条搜索结果都来自 Google，开源部署也不意味着自动继承云服务的搜索供应和额度。因此 Reader 可作为正文抽取的独立比较对象，但不能称为免费、无限、来源明确的 Google 搜索 API。[^29]

SearXNG 等元搜索聚合多个上游时，整体结果可用不等于 Google 上游可用。Google-only 验收必须保留每条 URL 的实际发现引擎、原始查询和首次发现时间，禁用其他引擎贡献或单列。公共实例的吞吐和稳定性不受本项目控制，不适合作为大规模免费代理池。

商业 SERP 服务能够外包部分 Google 访问、浏览器和代理维护成本，仍需验证自然结果数量、分页、语言、缓存、重试计费和来源。供应商套餐的“100,000 searches”不能当作“100,000 条合格正文”。本报告不以未经本项目验证的广告成功率或旧论坛价格估算采购成本。

成本应按最终接收计算：若每次实际搜索请求平均带来 1.5 条合格新增接收，10 万条需要约 66,667 次搜索请求；若平均只有 0.5 条，则需要 200,000 次。再叠加正文、代理、浏览器、存储和失败重试成本。这个数量级比“API 有没有免费档”更能决定方案可行性。

## 9. 与当前项目的具体对应

当前实现可以分为已有能力和本轮改动。已有 Google WML/浏览器 fallback、代理健康与全局配额继续保留。本轮没有照搬整套外部爬虫框架，而是在现有执行器旁加入 pipeline：正文进程常驻、会话复用、搜索/正文/上传解耦、域名公平调度、历史 URL 提前过滤及失败冷却。

| 当前改动 | 解决的问题 | 与外部思路的关系 |
| --- | --- | --- |
| 24 个按需常驻正文子进程 | 每篇启动 Python、重复建连接和缓存丢失 | 常驻 worker 与连接池的通用工程实践 |
| 3 个搜索线程与独立上传线程 | 搜索或上传等待阻塞正文回收 | 与社区“搜索和正文分离”相符，采用现有项目实现 |
| 单域最多 2 并发、调度前检查 | 一个慢域名占满执行槽 | 对当前执行器的具体修复 |
| 有效历史 URL 提前跳过、正文 hash 去重 | 重复搜索和重复抓取消耗资源 | 新 URL 优先的增量采集策略 |
| 100 topic、100 event、600 recent、300 site 查询 | 主题过宽、分页重合、正文供给不足 | `site:` 与时间范围切分，已有公开搜索语义支持 |
| 明确发布时间字段扩展 | 有正文但无法上传 Whale | 现有验收要求驱动，不伪造日期 |
| 1 秒检查、最多 50 条/批上传 | 已完成正文等待上传 | Outbox 与幂等接收继续复用 |
| 新执行器开关、固定源码快照 | 难以复现或回退 | 实验工程要求，与入口技巧无关 |

本轮基础方案见[两小时实验方案](pipeline-two-hour-plan-2026-09-15.md)。完整两小时结果需要以固定截止时间内的回执为准，不能使用中途成功率替代。当前所有新入口候选均未混入这轮实验，其可用性仍属于外部证据和后续验证范围。

固定两小时现已完成：有效新正文 11,206 条、Whale 新增接收 6,724 条，达到 8,334 条参照线的 80.7%；旧实验首两小时接收 395 条。当前仍有 4,482 条有效新正文因日期缺失不能上传；运行账本标记搜索冷却约 33.5 分钟，另有 1,194 次重复成功搜索页。5,541 条接收按首次发现归属站点查询，占总接收的 82.4%。这些结果支持优先修复调度、优化查询与日期有效率；完整口径和证据见[两小时实测报告](pipeline-two-hour-results-2026-09-15.md)。

另一个已经在本地账本中确认的损耗来自调度语义：WML 成功返回空页、备用通道冷却时，原代码把备用通道的状态扩大成全局暂停 60 秒。工作区已加入针对性修复及回归验证，让该查询页保持未确认并延后，同时允许其他查询继续使用健康通道；全局 Google 配额与熔断照常执行。正式实验使用的只读快照不包含这项后续修复，不能宣称两小时结果验证了它的提速幅度。

第二个已确认的损耗是浅层搜索页一小时后重新到期，反复取得大量旧链接。约 86 分钟的账本已有 881 次重复成功查询页。工作区已将 pipeline 成功页刷新间隔改为 24 小时，失败重试单独保留，优先处理未访问页；这同样不在固定实验快照内。它优先增加独立内容，会降低同一查询的日内刷新频率，适合当前允许扩展历史时间范围的新增量目标。

## 10. 下一轮验证设计与优先级

**第一优先：分析现有漏斗。** 按查询族、页码、站点和出口统计实际请求→有效 SERP→独立新 URL→相关有效正文→有效日期→Whale 新增接收。分别计算本小时新增、跨历史去重后的新增和失败重试。若正文进程经常空闲而上传无积压，优先增加高质量搜索供给；若正文很多但缺日期，优先改日期证据和站点选择。

**第二优先：有限配置对照。** 使用约 200 个分层查询，覆盖主题、事件、时间和站点，不只测试一个英文通用词。候选与基线交错运行，复用相同全局预算；先比较有效 SERP 和新 URL，再对差集执行正文和 Whale 验收。测试预算相同、计入失败和冷却时间，才能知道哪种方式提高总量。

**第三优先：常驻浏览器备选。** 只部署一个小规模对照，至少验证两个独立时间段。Google 返回空壳页、重复第一页、CAPTCHA 或需人工时分别记录。升级浏览器的价值必须超过额外内存、启动、页面渲染和代理流量成本，不能用浏览器网站的通用“反检测分数”代替结果质量。

**第四优先：独立来源补充。** News RSS 和自有 CSE 分开报告发现量及与 Web 的交集；只有确认来源范围和日期语义符合验收后，才讨论纳入日量。不以第三方公共 token 或共享免费实例维持生产依赖。

| 必报指标 | 判断用途 |
| --- | --- |
| 每千次实际 Google 请求的 Whale 独立新增接收 | 判断入口、查询和代理的真实收益 |
| 每分钟接收，包含冷却与失败时间 | 判断固定两小时和全天容量 |
| SERP 真实外链数、跨页重复率 | 排除 HTTP 200 空壳和伪分页 |
| 原站有效正文率、相关率、日期有效率 | 定位搜索后损耗 |
| 跨实验 URL/hash 重复率 | 排除反复取得旧数据的假增长 |
| 出口冷却时间与失败类型 | 判断增加配置是否只是增加请求 |
| 每千条接收的 CPU、内存、网络及付费成本 | 判断能否长期运行 |
| 前后半程及次日新增衰减 | 区分首轮挖掘存量与持续日增量 |

从两小时达到参照线，到每天持续 10 万条，仍隔着语料存量、历史去重和连续运行三项验证。时间范围扩展可以在初期释放旧文章，但不能据此断言每天都有等量新的相关内容。长期验收应在同一历史账本上连续多天进行。

## 证据边界

公开资料不能证明存在永久免验证或无限分页的 Google 免费入口。多配置、浏览器和住宅出口目前均缺少与本项目中文主题、Whale 日期要求和历史去重完全相同的独立长期实验。报告中的实施优先级是基于源码、正反案例和项目瓶颈的判断，不是对这些候选已经完成 Google 实测的声明。

中文讨论中的价格、调用无限和成功率表述按作者自报处理；无法取得正文的论坛页面不构成引用证据。日期以原文、评论或官方更新时间为准，不能用 GitHub 最近活动时间替代方法生效时间。

## Sources

[^1]: vojkovic / SearXNG. “[fix] google: use Nokia UA,” PR #6546，2026-08-18，2026-08-22 合并。<https://github.com/searxng/searxng/pull/6546>。12 小时 36,000 请求为作者自报。
[^2]: SearXNG. “Google fully blocks requests with Nokia User-Agent,” issue #6570，2026-08-23；后续配置讨论。<https://github.com/searxng/searxng/issues/6570>；<https://github.com/searxng/searxng/issues/6570#issuecomment-5384680496>。
[^3]: SearXNG. “GSA for iPhone useragent do no longer work,” issue #6359，2026-07-03 起的讨论及 Nokia 发现评论。<https://github.com/searxng/searxng/issues/6359>；<https://github.com/searxng/searxng/issues/6359#issuecomment-5114886182>。当前实现：<https://github.com/searxng/searxng/blob/master/searx/engines/google.py>，访问 2026-09-15。
[^4]: DonSeTch 贡献者. “feat(search): bring back fast, keyless Google search,” PR #167，2026-09-08，2026-09-09 合并。<https://github.com/dondai44423/donsetch/pull/167>。
[^5]: curl_cffi / lexiforest. “Impersonation FAQ”，访问 2026-09-15。<https://curl-cffi.readthedocs.io/en/latest/impersonate/faq.html>。
[^6]: V2EX. “解决 GOOGLE 搜索出现验证码”，主题 #329606 及回复，2016 年。<https://www.v2ex.com/t/329606>。
[^7]: ultrafunkamsterdam. “nodriver” 项目 README，访问 2026-09-15。<https://github.com/ultrafunkamsterdam/nodriver>。
[^8]: Kaliiiiiiiiii-Vinyzu. “Patchright” 项目 README，访问 2026-09-15。<https://github.com/Kaliiiiiiiiii-Vinyzu/patchright>。
[^9]: daijro. “Camoufox” 项目 README，访问 2026-09-15。<https://github.com/daijro/camoufox>。
[^10]: Ian L. Paterson. “Anti-Detect Browser Benchmark 2026: 7 Tools, 651 Verdicts”，2026-05，文中版本快照截至 2026-05-17。<https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/>。
[^11]: Ian L. Paterson. “anti-detect-browser-bench”，`bench.py` 分类器及结果集，访问 2026-09-15。<https://github.com/ianlpaterson/anti-detect-browser-bench>；<https://github.com/ianlpaterson/anti-detect-browser-bench/blob/master/bench.py>。
[^12]: 同上，第一轮 Google 原始记录，访问 2026-09-15。<https://raw.githubusercontent.com/ianlpaterson/anti-detect-browser-bench/master/results_run_1/records-curl_baseline.json>；<https://raw.githubusercontent.com/ianlpaterson/anti-detect-browser-bench/master/results_run_1/records-nodriver.json>。
[^13]: duanshiwen / V2EX. “给大家分享一个，我用 Python 重写的一个完全免费的 5 引擎搜索 MCP”，2026-06-01。<https://www.v2ex.com/t/1216958>。关联项目：<https://github.com/duanshiwen/seach-mcp-craft-agent>。
[^14]: SearXNG. `google_cse.py`；Google. “Programmable Search Element Control API”，访问 2026-09-15。<https://github.com/searxng/searxng/blob/master/searx/engines/google_cse.py>；<https://developers.google.com/custom-search/docs/element>。
[^15]: SearXNG. “Google CSE returning terrible results,” #6524，2026-08-14；“Google CSE Unexpected Regional Results,” #6441，2026-07-23。<https://github.com/searxng/searxng/issues/6524>；<https://github.com/searxng/searxng/issues/6441>。
[^16]: Google Programmable Search Engine Team. “Updates to our Web Search Products & Programmable Search Engine Capabilities”，2026-01-20。<https://programmablesearchengine.googleblog.com/2026/01/updates-to-our-web-search-products.html>。
[^17]: Google Developers. “Custom Search JSON API”，页面更新 2026-02-18，访问 2026-09-15。<https://developers.google.com/custom-search/v1/overview>。
[^18]: ranahaani. “GNews” README 与 RSS 处理源码，访问 2026-09-15。<https://github.com/ranahaani/GNews>。
[^19]: SSujitX. “google-news-url-decoder” README 与同步/异步解码实现，访问 2026-09-15。<https://github.com/SSujitX/google-news-url-decoder>。
[^20]: google-news-url-decoder 贡献者. “Add request timeouts, fix the async decoder's User-Agent,” PR #19，2026-08-24，访问时尚未合并。<https://github.com/SSujitX/google-news-url-decoder/pull/19>。
[^21]: SearXNG. “Google search internal API with JSON results,” #1642，2022-08-09；停用评论，2023-05-22。<https://github.com/searxng/searxng/issues/1642>；<https://github.com/searxng/searxng/issues/1642#issuecomment-1557005851>。
[^22]: SearXNG #6359. Google Docs Explore 搜索披露，2026-07-21；失效反馈，2026-08-21。<https://github.com/searxng/searxng/issues/6359#issuecomment-5036440576>；<https://github.com/searxng/searxng/issues/6359#issuecomment-5372580707>。
[^23]: benbusby / Whoogle Search. “Whoogle has reached the end of the road”，README 公告，2026-07-24。<https://github.com/benbusby/whoogle-search>。
[^24]: udm14.com，访问 2026-09-15。<https://udm14.com/>；Google Search Help. 搜索过滤器说明：<https://support.google.com/websearch/answer/2466433?hl=en>。
[^25]: Barry Schwartz / Search Engine Roundtable. “Google Search Testing Dropping 100 Search Results Parameter”，2025-09-12。<https://www.seroundtable.com/google-search-drops-100-results-parameter-40097.html>。
[^26]: Google. “Here’s an update on our use of country code top-level domains.”，2025-04-15。<https://blog.google/products-and-platforms/products/search/country-code-top-level-domains/>。
[^27]: Google Search Help. 搜索运算符与时间范围说明，访问 2026-09-15。<https://support.google.com/websearch/answer/2466433?hl=en>。
[^28]: SearXNG. `autocomplete.py`，Google 建议词适配实现，访问 2026-09-15。<https://github.com/searxng/searxng/blob/master/searx/autocomplete.py>。
[^29]: Jina AI. “Reader” README，访问 2026-09-15。<https://github.com/jina-ai/reader>。
