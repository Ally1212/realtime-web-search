# 原始响应实体私有留存：本地实现与限制（2026-11-06）

本轮实现可显式启用的原始响应实体加密存储，未部署、未启用生产、未创建生产密钥、未搜索/上传。此前同响应occurrence修复仍为本地候选；已暂存v1仍暂停。

## 实际实现

`PrivateResponseVault`使用显式传入的32字节密钥与AES-256-GCM；12字节随机nonce，AAD认证tenant/request UUID、状态、授权引用、期限、body hash/size及key ID。密钥不由本模块生成、落盘或输出。构造目标必须私有目录700、无symlink祖先，密文文件600，磁盘临时文件也只含密文。fsync后exclusive发布，已有请求相同绑定幂等，冲突不覆盖。

单实体最多64MiB、默认总配额2GiB，配额包含崩溃残留的加密临时文件；超过限制拒绝，不发布截断文件。expiry必须显式、时区明确且在24小时内，由调用方取实际许可/授权的更短期限；授权引用不是权限批准，模块不自证来源许可。到期拒读；purge_expired只对已认证且过期的当前vault文件unlink+目录fsync，不声称清除了备份/副本/物理介质。

保留的是requests.response.content的**解压后的HTTP实体bytes**，包括无效JSON/非UTF8/空字节；不是原始TCP/TLS或压缩传输字节，不包含HTTP头。状态另绑定。没有HTTP响应、传输中断或读取失败时不能凭空生成原字节，也不能把只有hash视为完整捕获。

OpenSERP transport的可选raw_response_recorder在读取完整response.content之后、状态/JSON校验之前调用，因此503与非JSON也能加密保存；默认没有sink，不保存原bytes。私有引用仅留在线程私有属性和可选source-proof私有日志，不进入last_serp_response、公开SQL结果或现有JSONL/CSV。成功响应的public upstream_response仍是过滤JSON投影，两者职责和完整性声明分开。

SingleWebSource增加显式sink注入；source-proof保留capture绑定与saved_request_ids。成功/正确echo示例两UUID一致，但失败且上游未返回ID时，发出请求的UUID与本地保存结果UUID可能不同：应按同一单次私有证据关联，不能把两个namespace自动视为一个ID。当前不是通用数据库原始字节索引或多tenant公开读取接口。

## 验证

10项vault/transport测试覆盖binary逐字节往返、文件无plaintext、身份冲突、错租户/错key、AAD/密文篡改、expiry拒读、大小/授权引用/期限、配额/加密orphan、symlink/权限、503非JSON与默认关闭、到期清理。新增真实隔离数据库+loopback HTTP source测试：503 body捕获/加密回读，HTTP状态和UUID绑定到私有证据，公开保存响应不含私有引用，backend仅一次请求。

本地相关58项通过；从上一阶段实际冻结worker最小候选加入新模块与明确三项Web验证支持，最终127项通过，0失败/跳过、0包外realtime模块、0非loopback socket连接。没有通过测试偷偷借用工作树源码。此前116项冻结证据仍为上一阶段，不覆盖本轮新功能。

本机实际cryptography为50.0.1，本轮pyproject显式声明cryptography>=50,<51；没有联网安装或改生产镜像。现有生产镜像版本是否满足此依赖尚未检查，不能宣称可直接上线。临时schema0残留，专用PostgreSQL已停止；测试密钥仅为内存fixture，临时密文目录随测试清理。

## 尚缺而不能宣称完成

- 真实来源/用途与raw保存期限批准、registry持续查权、撤权传播；authorization_ref只是外部决定引用。
- 生产密钥托管/轮换、key loss恢复、最小读取权限、备份与副本到期/撤权清除。
- 生产冻结依赖核验/独立候选发布、启用方式及真实响应捕获验收；默认依然关闭，不修改现有API/导出或许可证范围。
- 历史未保存的原字节/occurrence不可恢复；密文存在也不证明公开交付完整或平台已读回。

证据 `state/google-data-closure-20261106/raw-capture/verification-summary.json`、`frozen-tests.json/log`、候选源hash。映射GO-DAT-002/010、GO-ID-002、GO-ING-001/005及GO-NFR-007的局部实现，整体不标完整验收。外部Consumer 402/credit_insufficient、旧摘要异常、明确新运行授权、七天/真实百万行条件仍未满足。

后续生产只读前检已确认两镜像cryptography50.0.1及内存AESGCM操作可用，消除“镜像依赖版本未知”这一项；文件/密钥/许可等未因此完成。详见 `docs/google-private-response-runtime-preflight-2026-11-06.md`。未安装、未启用。
