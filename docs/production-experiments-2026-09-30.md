# 生产采集配置对照实验（2026-09-30 起）

目标：找到并固定"单日入库合格新正文篇数最大"的采集配置，之后 7×24 运行。

## 设计

两组极端配置，独立顺序运行，各自跑约 24 小时后对比：

- 实验 B（进行中）：纯 google_web（OpenSERP/wml 标准 Google 网页搜索）
- 实验 A（待运行）：全部打开 google_web + google_news + google_trends + rss

指标：尝试数 / 成功率 / CAPTCHA 率（google_web source 计数器）+ 入库正文数（campaign fetched/today）。
为避免与历史 campaign（人工智能/artificial intelligence）去重污染，两组均使用全新关键词集。
B 组关键词：5 个 campaign × 21 查询 = 105 个查询，主题：新能源汽车 / 中国股市A股 / NBA·体育 / 智能手机·半导体 / 电影票房·娱乐。
（单 campaign 21 查询 × 11 页 × 6 小时缓存 ≈ 38 页/小时，远低于 google_web rps 上限，故用多 campaign 提高查询多样性以接近通道产能。A 组将使用相同结构（5 campaign × 21 查询）但来源全开、关键词主题不同但体量相当。）
A 组将使用体量相当的另一组全新关键词。

## 运行记录

### 实验 B：纯 google_web

- campaign_ids：
  - a2a69949-68c9-4548-9611-fa811aa989d1（新能源汽车，13:32:22 UTC）
  - 153e2cb0-89a5-4b7d-8cf9-1720c3c36780（中国股市 A股，13:44 UTC）
  - 7632ff5a-5339-4cf7-84bf-06fb03c5da43（NBA/体育，13:44 UTC）
  - 0869942e-7c0a-42d0-a6a5-293aabe029b7（智能手机，13:44 UTC）
  - d3f76ad5-abf3-4d49-af9c-62dddf22bac2（电影票房，13:44 UTC）
- 启动：2026-09-30 13:32:22 UTC（API POST /api/local-campaigns，proxy_profile=private）
- 通道验证：启动后 google_wml healthy，rps 1.0，前 3 分钟 63 请求全成功、0 CAPTCHA
- 启动 4 分钟：discovered 518，fetched 17（入库 today 10）
- 基线计数器（启动前）：google_web requests_total=52250, successes=33972, captcha=7072

## 数据采集

服务器 cron 每 10 分钟快照一次 /api/stats：
`logs/experiment-snapshots.jsonl`（脚本 `logs/exp_snapshot.sh`，crontab 已登记）。

## 备注

- 网页入口实际映射为 127.0.0.1:8093（.env WEB_PORT=8093），AGENTS.md 中 8091 已过时。
- GOOGLE_FREE_PROVIDERS=wml（google_web 走 Google 轻量页面 + 代理池；openserp provider 当前 degraded 但未使用）。

## 实验 B 中期读数（启动 51 分钟，14:23 UTC）

- 入库有效正文合计 1520（新能源 327 / A股 207 / NBA 426 / 智能手机 337 / 电影票房 223）
- google_wml：约 680 次搜索/小时，成功率 99.8%，CAPTCHA 仅 1 次
- 单 campaign 初始爆发约 200-500 篇后回落，符合"21 查询 × 11 页 × 6h 缓存"模型

## 实验 A 预案（全来源，待 B 满 24h 后启动）

先停止 B 的 5 个 campaign，再创建 5 个 sources=[google_web,google_news,google_trends,rss] 的新 campaign，关键词主题（体量与 B 相当、互不重叠）：

1. 医疗健康：新药获批/疫苗/医保改革/癌症研究/基因编辑/医疗器械/中医/心理健康/减肥药物/糖尿病 + cancer research, vaccine news, FDA approval, mental health, gene editing, medical device, 养生, 医院, 医保, 药品降价
2. 游戏电竞：电竞赛事/英雄联盟/王者荣耀/原神/Steam/PS5/Switch/黑神话悟空/游戏版号/手游排行 + League of Legends, esports, Genshin Impact, game release, PlayStation, Nintendo, 单机游戏, 网络游戏, 虚拟主播, 游戏直播
3. 教育留学：考研/留学申请/公务员考试/教育改革/大学排名/托福雅思/职业教育/双减政策/在线教育/奖学金 + study abroad, college ranking, IELTS TOEFL, scholarship, online education, 中考, 幼儿园, 教育政策, 招生, 学费
4. 旅游出行：机票价格/酒店预订/高铁/签证政策/景区/出境游/民宿/航空公司/邮轮/自驾游 + travel news, airline, hotel deals, visa policy, cruise, tourism, 五一假期, 国庆旅游, 免签, 航班
5. 国际时事：俄乌冲突/中东局势/欧盟/联合国/北约/特朗普/中美关系/台海/朝鲜 + Ukraine war, Middle East, EU news, NATO, Trump, China US relations, 英国, 日本政治, 韩国总统, 印度

对比口径：各跑 24h，取 campaign fetched 合计 / today 合计、google_web source 尝试数·成功率·CAPTCHA 率。

## 实验 B 扩容（2026-09-30 15:29 UTC）

现象：14:50 起全部 105 查询 frontier=completed，系统闲置至 20:30（6h 缓存重复周期）。瓶颈是查询多样性，不是搜索速率。
处置：新增 15 个 google_web-only campaign（美妆/母婴/宠物/家居/美食/摩托/数码配件/户外/时尚/金融/求职/农业/法律/交通物流/极端天气），B 组现 20 campaign × 21 查询 = 420 查询。
实验 A 启动时也需扩到 20 个 campaign（同体量、不同主题）才可比。
代理同步出现 1 次瞬时失败（15:14 已恢复，80 代理 fresh）。

## 实验 B 第二次扩容（2026-09-30 ~19:05 UTC）

现象：18:30 UTC 起新增归零——420 查询全部完成首轮 frontier，系统闲置等待 6h 缓存刷新（20:30-21:30 UTC）。
结论：日产量 = Σ(各查询每 6h 首轮+刷新增量)，查询规模直接决定产量，且错峰不足导致闲置窗口。
处置：新增 19 个 google_web-only campaign（音乐/书籍/剧集/健身/心理/编程/AI工具/电商/二手车/楼市/编制招聘/酒类/养老/航空/酒店/漫威/短视频/船舶/航天），B 组现 39 campaign ≈ 819 查询。

## 缓存周期调整 + 第三次扩容（2026-10-01）

- 23:30 UTC 起将 GOOGLE_WEB_QUERY_CACHE_SECONDS 从 21600 → 7200（重启 local-worker 生效）：
  每查询每 2h 重新拉取前 3 页（深页仍 24h 缓存），刷新频次 ×3。CAPTCHA 率维持 ~0.5% 低位。
- 观察：刷新呈波浪（批次到期集中→爆发，然后波间低谷）；夜间低谷增量趋零属内容侧现象。
- 10-01 09:00 UTC 前后波间闲置约 1h，新增第四波 20 个 campaign（家电/医药/存储芯片/光伏储能/银行/券商/保险/基建/化工/有色/钢铁煤炭/汽车零部件/代工/机器人/云计算/网安/SaaS/通信/面板/电池材料），B 组现 59 campaign ≈ 1239 查询。
- 阶段日均值估算：约 900 篇/小时（10-01 UTC 日），外推约 2 万篇/天。
- 注意：today 计数器按 UTC 自然日归零，跨日分析用累计 fetched。

## 实验 B 24h 汇总（09-30 13:32 → 10-01 13:32 UTC，纯 google_web）

- 合格新正文入库约 3.13 万篇/24h（09-30 UTC 日 15569 + 10-01 UTC 日 15709），均值约 1300 篇/小时
- google_web 通道：17128 次搜索，成功率 ~99%，CAPTCHA 率 0.23%，80 私有代理全程可用
- 结论：产能随关键词池规模近似线性增长；瓶颈是查询多样性，非速率/验证码

## 实验 A 启动（10-01 13:37 UTC，全部来源）

- 方式：59 个 campaign 原地 UPDATE sources = [google_web, google_news, google_trends, rss]
  （保持关键词不变，B 的 24h 数据作为 google_web-only 基线；对比 A 切换后 google_news/google_trends/rss 的边际增量）
- 对比口径：/api/stats source_stats 按 campaign×source 的 today 计数；快照 jsonl 已含 google_news/google_trends/bing-* 计数器
