# OpenSERP 会话缓存修复

基于上游提交 `29c7b0fbe09640160efcfc1f1e04e60e0fbe60e9`、Go 1.24.6。

`openserp-lane-ownership.patch` 在归一化会话键时复制字符串，防止长生命周期 map 引用请求缓冲；LRU 删除未取得进展时清空会话缓存并返回，避免无限持锁。`openserp-lane-ownership-test.patch` 验证借用缓冲变化后已保存键仍不变及缓存容量限制。

服务器构建目录：`/home/azureuser/openserp-capacity-build/karust-openserp-29c7b0f`。构建：`go test ./core -run Lane -count=1 -timeout=90s`，然后 `CGO_ENABLED=0 go build -trimpath -o openserp-lane-fixed .`。运行镜像继承原生产镜像，只替换二进制，名称 `realtime-openserp:0.8.12-lane-fixed-20261007`。

这两处修复针对已定位的淘汰循环与借用字符串风险，尚不能证明历史故障确由请求缓冲复用触发，也不代表已经解决 Google 限流或产能达标。

回滚：恢复服务器 `compose.yaml.before-lane-fixed-20261007` 与 `openserp/config.yaml.before-lane-fixed-20261007`，重建 OpenSERP 容器。

`openserp-google-diagnostics.patch` 增加仅在结果解析失败时启用的本地 HTML 快照，通过 `OPENSERP_DIAGNOSTIC_DIR` 开关控制；不包含代理/请求头。

`openserp-google-ready-results.patch` 要求 Google 的宽泛结果容器包含 `h3`，排除只有分享按钮的初始搜索壳。真实失败 DOM 的旧容器匹配数为 1、结果标题数为 0，导致立即进入结果解析并失败；新选择器让已有的等待逻辑继续等待结果标题。配套 fixture 测试验证搜索壳不被当作结果，以及新布局文章结果仍可识别。是否提升搜索成功率仍需固定配置实测。
