> 当前状态更新：v1执行已因同响应occurrence丢失缺口暂停，旧advance命令即使提供hash也会拒绝。本文原命令保留为历史待执行记录；见 `docs/google-occurrence-integrity-2026-11-06.md`，不可据旧清单启动。

# 单次Web真实技术验收：待执行清单（2026-11-06）

**尚未授权、尚未执行搜索或平台写入。** 以下是现有OpenSERP/Whale链路的一次受限技术验收，不批准正式来源/用途/schema/ReaderAdmission，不开启持续供给、正文、自动上传、平台领取或监测。

## 已经完成的准备

独立sidecar暂存在采集机 `/home/azureuser/projects/realtime-web-search-openserp/state/web-once-20261106-v1`（容器路径 `/app/state/web-once-20261106-v1`）。51个文件与本地冻结候选逐一SHA匹配，非新增文件还与实际运行包逐一核对。源码文件600、目录700，未覆盖 `/app/realtime`、未改Compose、未重启任何容器。包SHA256 `2ef825d8cb7f6714cb03d029f977e580f069584ae8bfaeb59bf32fca27b1082f`。

实际采集容器中plan与preflight均exit0；执行清单已绑定现有Dataset/source/collector、OpenSERP endpoint、私有代理profile、数据库目标指纹、全部Python源码hash与独立journal目录，完整清单保存在 `state/google-data-closure-20261106/web-once/staging/actual-plan-preflight.json`。执行清单SHA256：

`dbbc29288c5d2d7a0c0902f126306abb14645583a0c9978c18bc3a9717b9bd06`

已关闭四开关，七项在途/监测计数为0。暂存及计划执行前后数据库全部业务字段、两机运行源码、七个相关服务ID/启动时间/镜像/挂载内容不变；原请求39840、pages332768、旧outbox9queued仍保留。Docker挂载列表有顺序变化，原始快照保留，规范化排序后内容一致。此比较没有伪称逐字JSON相同。

## 请求用户授权的准确范围

1. `solar energy research 2026`，Google Web / 既有OpenSERP，US/en，1页，最多一次backend HTTP搜索；不得自动改关键词、换源或追加请求。OpenSERP内部尝试/缓存状态仍以真实返回记录，不承诺精确设备/位置或强制fresh。
2. 保存成功或失败的原结果及独立occurrence/URL信息；任务始终paused，正文关闭。只有成功、完整保存、条件与身份通过核对且平台dry-run有效，才把**这一条**保存快照经既有whale.ingest.v1完整run envelope、真实搜索文本投影发一次单消息bulk。失败/无结果/拒收/未知不补第二次新搜索或写入，不扩写正文凑评分。
3. 使用已有Writer/Consumer身份，无新Key/外部权限变更。为此一次验收，将本机已有Consumer Key仅通过SSH stdin→docker stdin送入执行进程内存；不入argv、磁盘或输出。查询同一稳定事件终态，最多10次只读receipt，遵守429/503冷却；独立Consumer固定版本核对原响应、摘要、全部结果和hash。queued不算完成。
4. 完成后保持所有自动开关false；保留成功与异常证据，不覆盖9条旧queued或旧异常版本。未知intent停止写操作，仅保留现状等待只读对账，不把超时当失败。

这仅请求一次既有来源链路技术验收，不包含News/Trends新采集、批量上传、付费来源、七天运行或真实百万行任务，也不替代这些验收。来源/用途正式决定及旧摘要异常仍独立未解决。

## 已准备的启动/停止方式

仅计划/只读前检（本轮已执行preflight）：

```sh
.venv/bin/python scripts/google_web_once_remote.py plan
.venv/bin/python scripts/google_web_once_remote.py preflight
```

只有用户明确同意上面的单次范围后，才执行：

```sh
.venv/bin/python scripts/google_web_once_remote.py advance --authorized-manifest-hash dbbc29288c5d2d7a0c0902f126306abb14645583a0c9978c18bc3a9717b9bd06
```

启动器拒绝没有精确清单哈希的advance，源码/配置变动会使容器内清单校验失败。仅一次推进；awaiting_terminal或receipt_not_before时按真实状态再做同journal只读推进，不自动创建另一个journal。请求超时后不能据此重新搜索/上传。人工停止标志位于 `/app/state/web-once-20261106-v1/run/STOP`（真实run目录本轮未创建）；动作间检查，不能撤销已经发出的HTTP请求。

私有sidecar为待验收工具刻意保留；运行包及常驻服务不依赖它。若放弃验收，先核对工具目录/绑定及不存在执行进程，再仅清除此sidecar，不动生产运行目录或历史证据。

验证证据：`state/google-data-closure-20261106/web-once/staging/verification-summary.json`、`actual-plan-preflight.json`、`no-business-change.json`及前后快照。工具既有冻结49项通过，本轮受限启动器2项通过（不相加冒充平台真实验收）；没有运行新Google/Whale写入。

当前新增外部阻塞：Consumer普通文档读取真实返回402/credit_insufficient。受限启动器已加读前检，失败不进入mutating CLI；此前plan/数据库前检通过不证明平台可读。一次新搜索/上传仍待授权，且须先恢复既有Consumer读取能力；不自动充值或改权限。详见 `docs/google-legacy-anomaly-access-2026-11-06.md`。

单次候选已更新为独立v2，含当前occurrence两文件，77项隔离通过及真实plan/preflight通过。新hash04f34264d025ce5bc6c7694c37781d5c7390a12d18652d4addb2ab767a36a01d，入口仍禁用；平台额度/明确运行授权未解决。旧v1清单不再使用；见google-web-once-v2-readiness-2026-11-06.md。
