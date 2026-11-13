# 首页首次加载优化（已发布）

根因：workspace-refinement.js 首次进入所有页面时都添加 sf-view-loading，CSS隐藏该section的所有子内容；首页Promise.all同时等待最近结果和近90天历史汇总。实测汇总接口5.364秒，连不依赖数据的主标题/流程图/来源入口也被隐藏。

修复：首页不再套整页隐藏状态，保留已有统计卡片与最近结果各自的加载状态；数据请求、统计口径、60秒复用、错误提示、其他模块首载骨架保持原逻辑。更新本地生成器引用资产realtime/assets/workspace-refinement.js；正式static/index.html只修改同一条件，无服务重启或业务接口变更。

## 测量

同一ego浏览器，禁用HTTP缓存，正式登录态：
- 修复前首次：连接1.092秒，TTFB1.280秒，HTML结束2.093秒，load2.140秒，FCP1.620秒；主标题LCP7.392秒。统计请求5.364秒。
- 修复后：TTFB0.195秒，HTML结束1.219秒，load1.245秒，FCP0.456秒；主标题可见1.285秒（汇总仍aria-busy=true），LCP1.336秒。
- 首次连接与传输不同，不将7.392→1.336直接当作稳定加速比例。可直接归因的页面已加载后等待由约5.25秒降至约0.09秒；确定性回归证明统计Promise不完成时首页仍可见。

最终统计实际显示“已汇总公开数据”，无伪造缓存或删统计。390px复测汇总仍加载时主内容可见、无水平溢出；快速使用/首页往返验证通过。

## 验证与发布

Node执行实际refreshView函数的异步测试先失败后通过，覆盖统计挂起时首页可见及其他页面仍有首载状态。5项相关回归通过，无跳过（home_first_paint、workspace_refinement、component_ui_bridge、quick_request_flow、maps_ui_flow）。未运行与此静态修改无关的数据库全库测试。

CAS原SHA317570007093b0cd0a671cc3260edf1d6858478c49f3d9dfa6fe562878e2c826；新SHA0b47af649d87435cb940124c9a97b9f95db9131219d9e12a6846f71bc3f623bb，正式HTTP200与哈希核验通过。应用机备份：/home/azureuser/projects/google-workbench/state/home-firstload-20261113/backup/index.html。按精确哈希校验后原子替换，可用备份回退。未覆盖业务生成器或部署当前工作树其他改动。

证据output/home-performance-20261113/：before.html、after.html、after-desktop.json、publish.py。独立浏览器337完成后finish。无新搜索、数据库变更或后端重启。页面仍约1.07MB（gzip约302KB），初次TLS延迟与汇总查询自身耗时未被本次修改消除。

Modern Web Guidance：已读performance指南，按“DON'T implement complex JavaScript loaders for the hero section”解除首页主体对非关键数据的等待；使用既有骨架和原生Promise行为，无新增API或兼容性回退需求。

## 重新验证与版本归档

再次运行5项相关回归全部通过；在临时目录还原旧条件，原症状断言失败。正式HTTP读取的SHA与候选一致。新一轮禁用HTTP缓存：TTFB4.656秒、HTML结束5.725秒、主体可见5.772秒；此时汇总仍在加载，汇总9.889秒完成。因此整页等待修复有效，但不能保证每次首开1.3秒，连接和首字节耗时仍待排查。

可重复部署入口：`python3 scripts/deploy_home_first_paint.py /home/azureuser/projects/google-workbench`。只接受上述旧/新哈希；新版直接核验返回，旧版备份后原子替换，未知版本拒绝覆盖。本提交归档现有workspace-refinement资产与回归，不包含其他尚未提交的工作台生成器/中台改动；正式发布沿用已上线静态页，不从不完整历史源码重建整站。
