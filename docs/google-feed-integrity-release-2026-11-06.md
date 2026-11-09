# Feed保存完整性五文件上线（2026-11-06）

已按冻结候选发布：Web三个文件google_feeds.py/google_data.py/google_legacy.py，worker两个文件google_feeds.py/google_data.py。先更新Web读端并重启web/prd，核验通过后更新producer并只重启local-worker。其余四个采集服务ID/启动时间/镜像/挂载不变。没有采集/平台调用、新Key、原字节留存启用、旧正文/hash迁移脚本或Compose变更。

## 已上线行为与边界

- 无效RSS链接条目保留解析问题及真实列表位置；新响应带google-feed-observations-1标记。Trends报道原child位置和重复occurrence保留，URL仍独立去重。旧无标记记录的身份规则不变。
- News全部无效不再是成功empty，保存parse_error/unusable_feed_items并按失败缓存；读取/导出允许实际已存在的parse_error/http_error/rate_limited/transport_error/captcha分类。
- 未抓出版社正文，RSS顺序不作Google自然排名，Trending Now不作数值曲线。只保留已解析字段/问题，完整RSS XML字节及私有raw生命周期仍未完成。

包SHA256 `f83913ea7649004233cd07afa5ad0300c421b720543412c3408519a6ad359bdc`。两机各target before/after、保护源码与服务状态均核对通过；保留各机原有Images、publication/时间写入等差异，未覆盖web.py/serp_page.py或任何测试支持模块。具体五文件hash见同目录candidate-manifest及release各inventory。

## 验证

候选此前worker48/Web48分别通过，组间重叠不加总。本次新增生产原生包进程内fixture（两机各一）：News无效首项→有效项ordinal2且issue1；Trends无效报道后同URL两次出现child2/3、occurrence2、URL1；全无效News为parse_error。所有HTTP/SQL存储调用均mock，socket/DNS审计0尝试，没有把fixture写入生产数据库。

正式HTTPS5项通过：无认证401、健康200；正确客户身份下News parse_error、Trends http_error、Web captcha筛选200且条件回显正确。前两类在现有公开库样本0条，Web captcha1条；0条只证明查询成功且当前没有匹配公开样本，不冒充新格式真实数据验收。原有访问门和私有隔离没有放开。

三容器running、OOM false、restart_count0、新启动日志Traceback0。发布前、每机重启后、fixture/HTTPS后选定业务快照字段全部一致：请求39840（Web31679/News8127/Trends29/Images5）、pages332768、旧outbox9queued，四开关false，手动任务/分页/查询/上传lease/客户在途/启用监测均0；registry0，spool/ledger/domain export表仍不存在。认证GET可能更新正常访问元数据，本结论不是整个数据库逐字不变。

## 回退与证据

两台各自项目 `state/feed-integrity-release-20261106-0940` 有精确原文件、manifest/journal、绑定本五文件包SHA的通用release-functions与rollback.py，600/700。用90秒内完整只读前检JSON执行rollback.py，先核对所有当前old/new hash、保护文件和备份，再恢复；随后仅重启同一服务并重复核验。当前未执行生产回退，不恢复旧Compose、不删除历史或回滚数据库。

若后续已经产生新版本Feed记录，回退读取模块前需重新检查旧读端的兼容性，不能把旧读端解释结果当新数据完整性证明；可保持前向兼容读端，避免盲退。本轮没有创建这种生产数据。

本地证据 `state/google-data-closure-20261106/feed-integrity/frozen/release/`：initial/final DB、两机initial/before/after/final源码与服务、installed/restart/verification、native fixtures/health、HTTPS smoke及verification-summary。新备份不复用旧有mutation marker程序。

## 尚未完成

正式来源/用途与ReaderAdmission、完整原字节留存/密钥/备份许可、旧摘要异常、Consumer最近402/credit_insufficient、一次新运行明确授权、每来源真实交付/固定版本、七天与真实百万行均未完成。v2独立Web执行仍禁用，本轮未改写或解禁它。

本次仅为GO-SRC-007、GO-DAT-002/010、GO-ID-002/006、GO-ING-001、GO-POC-05增加精确上线/读取证据，不提升整项为真实全需求验收。下一步核对全局剩余条件，继续不依赖真实运行授权的工作，不以公开历史/模拟数据代替新采集闭环。
