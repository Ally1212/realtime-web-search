# 网站订阅交互优化与发布（2026-11-10）

修复既有订阅界面的异步选择竞争：进度与记录采用独立选择版本，列表刷新/空间切换使旧响应失效；领取和历史详情采用批次选择版本，迟到响应不能覆盖新选择；下载绑定当前批次对象和版本，旧文件不弹出保存，也不误启用新批次确认。查看已确认/丢弃批次仍可下载，确认保持禁用。按钮改为“领取待确认批次”，提示切换后重新下载当前批次。

正式真实文件复验发现64位SHA256文本在390px撑宽页面；追加批次元数据换行补丁，使用真实历史批次复验。原始表、API语义、确认账本和权限未改。

## 验证

- 新增执行真实JS处理器的乱序响应回归，7种情形。旧版本在第一种情形复现 A 迟到覆盖 B，新版本通过。
- 独立PG/Redis相关回归30 passed、0 skipped；补丁后UI与下载客户端5 passed（与30项重叠）。第一次回归命令误写不存在的test_product_http.py，未执行测试；已更正为test_product.py后全部通过。所有隔离服务由脚本停止。
- ego空间130本地组合页面模拟旧下载迟到：当前新批次确认保持禁用；真实浏览器非完整生产写入回归。正式仅连接既有客户空间、读取暂停订阅进度/历史、重新下载已确认批次，无新搜索/订阅/领取/确认。
- 正式历史批次4请求/18事件，JSONL SHA256仍为`f965a8a64ffcefc2f58bdabf6b5ac71aba593731eeeb09baceeab2f728acbbdb`；下载后已确认按钮保持禁用。
- 发布前后只读快照一致：serp_requests39962、pages332768、sf_analytics_requests39934、Whale outbox9；continuous/content/uploads/claims均false，活动jobs/monitors/leases均0。

## 发布与回退

仅发布后台`code/realtime/subscription_ui.py`和应用机`static/index.html`。基于生产实际文件生成候选，静态页面保留已有“工作空间”用语及所有其他已上线模块，不用本地新能力工作树生成整站。只重启后台web，原PRD、insights及采集五容器不重建；安装器核对四个保护文件及采集五容器ID/启动时间。

两机原项目下`state/website-optimization-20261110/`保留原版本；追加换行补丁前版本在`state/website-optimization-20261110-mobile/`。恢复两机首轮backup文件并仅重启web即可回退到本轮之前；不回滚数据库、不重放旧安装脚本。源码未提交Git。

静态首轮安装因目标目录root权限失败在临时文件创建之前，原文件未变，备份已建立；检查备份摘要后通过sudo完成发布。无放宽目录权限。最终候选摘要见`state/website-optimization-20261110-mobile/manifest.json`；首轮回归、浏览器及前后快照见`state/website-optimization-20261110/`。

最终正式浏览器：320px及390px的clientWidth/scrollWidth/innerWidth分别完全相等，真实批次元数据overflow-wrap=anywhere，已确认按钮均禁用；正式HTTPS页面SHA256=`5ae440e505d0fa5a5046ae43ec6ec9eb7689f7dde8162ba6d344d4fddbd3c724`与最终候选一致。最终数据库快照与首轮发布前逐字节相同。ego空间130已finish且列表中消失，本地8898预览已停止；无新增长期任务。
