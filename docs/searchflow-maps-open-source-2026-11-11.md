# Maps 开源自建研究与真实验证

> 后续实施：自建Maps查询、页面与导出已上线；中台尚未接通。见 [上线记录](searchflow-maps-integration-2026-11-11.md)。

日期：2026-11-11。用户已否决 Google Cloud 付款资料方案，并要求研究无需注册第三方 Key 的开源路径。

## 结论

推荐以 [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) 作为独立采集后端，先接商家搜索。已在采集机隔离运行 v1.18.2 的 HTTP fast mode，真实获得 17 家商家。此结论只证明一次查询可用，尚未接入网站、Marsyoo 或部署常驻 Maps 服务，不代表长期稳定性、完整详情或城市全量覆盖。

## 候选对比

| 项目 | 已查证的技术路径 | 判断 |
| --- | --- | --- |
| [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) | MIT；CLI、Docker、REST、CSV/JSON、代理；普通模式用浏览器，fast mode 用 HTTP | 首选；本轮只实测 fast mode，无需 Google/第三方 Key |
| [Mahanaicoach/google-maps-scraper-kit](https://github.com/Mahanaicoach/google-maps-scraper-kit) | MIT；Python + Chrome；REST、CSV、可选网站信息扩展 | 备选；源码审查，未执行；不能照搬作者吞吐宣称 |
| [omkarcloud/google-maps-scraper](https://github.com/omkarcloud/google-maps-scraper) | 仓库 MIT，但当前 README 主推带账号、验证码、月度免费额度的产品 | 不优先；不能把仓库开源等同于整套服务无需账号/无限免费 |
| [jinef-john/google-maps-scraper](https://github.com/jinef-john/google-maps-scraper) | HTTP/Python 方案；检索时 license 元数据为空 | 暂不选，集成许可未核实，未执行 |

审阅版本：gosom release commit `146aefe83844960bb534f335261a4435783a58cc`；kit `e370d0cb2a255fee3cd894e4ef1ba9aa170191e1`；omkar `7eae385588a0320e51bb7a5193f53a7d30e896d8`。

另一个方向是 OpenStreetMap 自建地点搜索，但它提供的是另一套地图数据，不能替代 Google 的评分、评价和商家覆盖。若后续接受更换数据来源，可单独评估；本轮未实测该路径。

## 实测证据

- 官方 Linux amd64 发布二进制：v1.18.2，SHA256 `468eec927939b3bcbdd94c59d6d7eef7472b5365409d66545bbe78cc910b309f`。本机与服务器文件 hash 一致；不是发布者签名校验。
- 查询：`coffee in Singapore`；圆心 `1.29027,103.851959`，半径 5,000 米，zoom 15，语言 en。
- 独立临时容器：单并发、512MB 内存、0.5 CPU、64 PID 上限；无生产数据库/密钥挂载、无公网端口；关闭遥测，不配置代理，不启动内置浏览器。
- 参数：`-fast-mode -c 1 -depth 1 -json -exit-on-inactivity 20s`，外层 100 秒超时。日志一条请求成功，记录请求耗时约 1.58 秒，不是长期平均性能。
- 输出 17 行、17 个不同 data_id；坐标合法，均在 5km 内，最远约 2,619 米，按距离升序。此顺序不能当作 Google 原始排名。
- 样本名称包括 Butler Koffee、Upshot Specialty Coffee。尚未通过 Google 页面逐条人工核实字段准确性。

| 非空字段 | 条数 / 17 |
| --- | --- |
| 名称、地址、类别、网站、评分、评价数、经纬度、营业时间、data_id | 各 17 |
| 电话 | 12 |
| place_id、Maps 链接、营业状态、评论正文 | 各 0 |

非空只表示有值，不代表字段已独立核实；缺失电话不表示商家没有电话。完整原始结果和日志保存在 `output/maps-open-source-research-20261111/`，校验摘要为 `verification.json`。原始结果 SHA256 `0f15bb1fdf732582e94c171545c55b669152385581016e88b7b9f24e82d7c251`。

## 接入现有系统的具体方式

推荐链路：网站快速请求 → SearchFlow Maps 异步任务 → 独立 gosom 运行器 → 标准化商家结果 → 网页/JSON/CSV。Marsyoo 的 Maps source/dataset 和文档契约须核实后，再接入中台完成回执与读回校验。

1. 用 provider 适配器隔离 gosom，不把它返回值塞入官方 Places 短效详情模型。保存 provider/version、查询、坐标、采集时间和原始字段，`data_id` 与 `place_id` 分开。
2. 展平上游输出（本次是一行 JSON 数组），兼容 `longitude` 与遗留 `longtitude`。缺失保持 null；默认数字 0 必须结合解析证据判断，不能直接当真实评分或评价数。
3. 复用服务端快速请求空间，网页不要求用户填客户 Key；保留同源保护、任务收据、租户隔离、幂等、限额、超时和失败退款。已有 `/api/maps/*` 本地补丁尚未接入路由，也未上线。
4. 首版只开放低并发按需商家搜索，输入关键词、位置、半径和语言。快速模式每次最多约 21 条、Beta，不能承诺全量分页、完整评论、地点详情或商业 SLA。
5. 在获得数据用途与中台契约依据后，开启相应保存、导出和上传功能。MIT 是软件代码许可，不等于 Google 数据的存储或再分发许可。

完整浏览器模式能否补齐字段须另行验证；本轮未运行 Chrome/Playwright，也未改变用户指定的 ego 浏览器工作流。遇到验证码或明确拦截应中止并报告，不把空结果当成功。

## 运行与清理

本轮没有修改生产服务、开启自动采集/正文/Whale全局开关，没有新增付费账号或 API Key。临时容器退出后自动删除，已核验无残留 POC 容器；原始证据回收到本地，远端临时程序与输出目录已删除并核验。此前交给用户的 Google Cloud / Serper 页面保留，不关闭用户参与过的页面。
