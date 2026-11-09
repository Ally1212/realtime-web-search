# News / Trending Now 私有响应实体入口（2026-11-06）

关联 GO-SRC-006/007、GO-DAT-002/010、GO-ID-002、GO-NFR-007。仅本地实现和隔离验证；未部署、未启用、未登记新来源/授权、未创建生产密钥、未访问Google Feed或平台。

## 已确认缺口与改动

`parse_feed` 仅映射选定RSS字段，未知XML节点/属性、原始描述以及无效XML/HTTP失败实体不在公共响应中。不能从旧映射反推完整原XML，也不能把已有选定字段验收称为原响应完整保存。

本地 `collect` 新增显式 `discovery.raw_feed_response_recorder` 注入点；默认不存在，不捕获原实体，不覆盖现有公共字段。完整有界读取完成后、HTTP状态/XML校验前调用sink，传入本地UUID、source/query/locale、HTTP状态与解码后实体bytes。引用必须匹配UUID/status/body size/SHA256/representation；缺失或不一致会拒绝，避免无效引用被记为完整捕获。可将sink接入已有 `PrivateResponseVault`，但本次不提供生产密钥或启用配置。

捕获保留未映射XML、原始转义及无效字节；不是wire压缩包、TLS、HTTP头或出版者正文。超限/中断读取不保存部分实体，也不标为完整。原读取上限及错误分类沿用现有逻辑。sink失败抛出 `FeedCaptureError`，不缓存成功/失败，也不继续解析；HTTP响应关闭、查询lease释放。此时来源请求可能已经发生，不能当成未请求而自动重放。

私有引用和source/query/locale关联只保存在 `_thread_state.last_private_feed_observation`，不写入公共响应、API或导出。显式捕获时将同一UUID交给现有 `serp.record(upstream_request_id=...)`，私有证据记录实际saved_request_id。数据库写失败时保留capture与未完成关联，不能将密文存在当作已保存数据库记录。缓存命中清除本线程旧引用，不访问来源、不重复捕获；其他线程独立。

## 验证范围

`tests/test_google_feed_raw_capture.py` 覆盖两来源未知XML/非UTF8/503完整bytes、引用校验、sink失败、默认关闭、缓存命中、超限/中断与数据库失败；实际临时加密文件逐字节回读。独立PostgreSQL测试覆盖每来源成功/无效XML/503共6个合成响应，捕获UUID与保存请求UUID一致；已保存记录、规范化API和三格式导出一致，公开结果没有私有capture ID或未映射原文，缓存再读不新增来源请求。

初次新测试使用macOS临时目录未resolve，触发vault既有symlink祖先拒绝；改为resolve测试目录，未放宽存储路径规则。相关45项通过（包含既有Feed与vault测试），实际源HTTP由Mock返回，未用这些合成样本冒充真实来源验收。

证据目录 `state/google-data-closure-20261106/feed-raw-capture/`：`unit.log`、`targeted.log`、完整隔离回归及清理记录。工作树完整隔离回归971项通过、0失败/错误/跳过，审计记录非loopback连接尝试0；审计钩子覆盖父进程，不能据此推断任意子进程的网络行为。测试PostgreSQL/Redis已停止并确认端口关闭。未发布的本地改动不覆盖生产冻结包结论。

## 尚未满足的条件

- 必须先确认逐来源raw保存用途/权利/期限与registry持续查权；authorization_ref本身不是批准。默认关闭，不自动连接平台或扩大保留期限。
- key托管/轮换、最小读取权限、备份/副本到期及撤销删除、私有source证据持久化与恢复对账尚未完成。线程引用不是耐久事务日志，不能宣称跨进程crash后自动关联或无孤儿。
- 本轮未冻结/上线此新入口；生产仍只保存已有解析投影。实际新Feed采集、独立平台交付/Consumer固定版本读回、七天与真实规模验收仍未完成。旧数据原始实体不可恢复。

## 后续局部实现

已新增显式固定attempt的加密分阶段记录与只读恢复对账，见 `google-feed-capture-recovery-2026-11-06.md`。旧线程引用仍不是事务日志；只有显式接入新journal的本地路径新增了耐久关联，尚未部署。
