# 私有响应留存：生产依赖只读前检（2026-11-06）

两台既有Web/worker镜像只读检查均为Python3.12.14、cryptography50.0.1、requests2.34.2、psycopg3.3.6，AESGCM可导入，满足本地新声明cryptography>=50,<51。没有安装/升级包或重建镜像。

将本地google_raw_capture模块源码经SSH/docker stdin在各容器内存编译导入；用显式synthetic fixture做AESGCM binary往返与AAD篡改拒绝均通过，候选源码hash为 `17c8ea354376d9dfe4d0922eeb56e9f16996d67743a3aee208f666ce62b34e1b`。没有实例化生产vault、写文件或生产key，也没有源/平台网络调用。初次fixture key字面量实际33bytes导致assert停止，改成32bytes后通过；这是测试输入错误，非运行库失败。

本轮检查只证明同一运行环境能导入候选模块、运行所用密码原语；不替代上一轮本地127项文件/数据库测试，更不证明容器文件系统、容量、key托管、TTL/撤权/备份流程或真实来源已验收。

UTC09:01只读数据库快照：sf_google_registry仍0条；continuous/content_fetch/uploads/claims均false。没有据此断言所有外部合同不存在，只是正式registry尚没有可供执行器核验的来源/用途批准。因此不能仅凭某个authorization_ref字符串、代码可用或依赖已安装而启用原字节留存。

本次没有部署、重启、生成生产密钥、创建新任务或平台调用。平台最近一次实测仍为402/credit_insufficient（本轮未重复请求）；旧摘要异常和真实运行授权也未解决。新模块默认关闭，旧v1单次执行继续暂停。

下一项可独立推进的是已测试的同响应occurrence最小修复：保持raw sink未配置、采集关闭，仅更新精确冻结两文件并做发布前后不变性验证。原字节能力需另外完成明确的许可/期限、registry持续查权、生产密钥与备份生命周期，不能混入为默认开启。

证据：`state/google-data-closure-20261106/raw-capture/dependency-preflight/`中的worker/web package metadata、memory-check、registry-readonly与verification-summary。全部为新增独立证据，未覆盖旧快照。
