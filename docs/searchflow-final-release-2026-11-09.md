# SearchFlow 统一发布与小范围数据自测

用户于本轮明确授权提交、推送、更新网站并小范围自测，覆盖此前等待统一部署的 hold。功能提交 `c4215ea` 已推送 origin/main。

## 发布内容

- 采集机独立后台更新八个候选文件：product.py、product_http.py、google_data_export.py、customer_data.py、customer_export.py、customer_http.py、customer_jobs.py、customer_schema.py。以实际生产冻结代码合并，未将整个本地工作树覆盖生产。
- Worker 仅更新 google_news_links.py 的未知 Google News 路径保护；其余三个 News 候选文件与生产已经一致。重启专属后台 web/prd 与 local-worker；原 OpenSERP、proxy-relay、PostgreSQL、Valkey 未重启。
- 三个客户读取索引 CONCURRENTLY 建立成功。未运行历史正文/hash 迁移，web.py、serp_page.py、serp_console.py、google_http.py 哈希保持。
- 应用机 static/index.html 与冻结后台生成的最新 UI 字节一致，因此不重复改写静态页。应用机登录、反代和内网架构保留。
- 远端发布记录和逐文件回退备份：采集机 `/home/azureuser/projects/google-workbench-backend/state/final-release-20261109/`；独立安装包 `/home/azureuser/searchflow-final-release-20261109/`。旧带 marker 的安装脚本未重放。

## 验证

全量隔离回归 1087 passed、333 subtests；实际冻结后台候选 33 passed、7 subtests，冻结 worker 候选 65 passed、4 subtests，均无失败。临时数据库与 Redis 已由回归脚本关闭。

正式 HTTPS 通过 ego-browser 验证首页/数据集实际保存内容、API 路由与以下样本：

| 样本 | occurrence | 话题 | 完整 CSV 行数（含 request/feature） |
| --- | ---: | ---: | ---: |
| 已有 Web 客户任务 | 4 | 0 | 6 |
| 已有 News 客户任务 | 34 | 0 | 35 |
| 已有 Trends 客户任务 | 30 | 10 | 41 |
| 已有 Images 客户任务 | 20 | 0 | 21 |
| 本轮单次 News 新任务 | 10 | 0 | 11 |

五个样本的 API JSON 与完整 JSONL 一致，CSV 均为74列，request/result/topic 的 record_json 逐字段一致。公开 News ZIP 限两请求，159 occurrence，bundle 校验及 full.csv SHA-256 校验通过；truncated=true 明确保留，不能称全库完整导出。

仅创建一个真实客户 News 任务 `0e7a038e-ce95-4b63-8cd9-0921aa3f0a2a`，请求 `da2ec229-2133-4628-b73f-4296946fa80f`。非缓存成功，10条新闻均解析文章地址，新增8个历史去重URL；20次Google解析HTTP请求，无出版社抓取。幂等重放复用相同ID，用量最终6、reserved=0。首次携带未支持depth参数返回400且未创建任务，去除参数后才成功创建；没有隐藏额外搜索。

无客户Bearer读取401；客户写全站设置401；私有新请求通过公开legacy-data/requests详情404。网页显示持续采集暂停及真实新闻文章地址。浏览器独立Space 52已结束并核验移除。

## 最终数据和边界

两次最终只读数据库快照一致：请求39962（仅比发布前增加1），pages332768，公开分析39934，旧Whale outbox9。continuous/body/uploads/claims均false，启用监测、活动客户任务和在途lease均0；未新增平台上传。

本次真实新采集仅覆盖一条News任务；其他三来源是已有数据读取与导出验证，不作为长期成功率或产能证明。历史News包装地址未批量回填，ZIP样本仍保留未解析历史数据；未配置研究来源仍不可用。

本地证据：`state/release-20261109/`，包含测试、前后代码/静态备份、发布清单、安装日志、API与导出、幂等和权限结果、两次最终数据库快照。
