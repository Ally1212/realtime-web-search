# UI 反馈修正（已部署）

已部署至正式站，SHA256 a1074d77126efc8204580c94a4b117c5b82d4e229ec55cede0f7a152741efa0d，1,104,239字节。以下连接阻塞段为发布前历史记录，已解决。

- 首页流动轨道加粗，双数据包连续流动，三个节点分时响应；仍标注“数据流转示意”，不冒充实时采集。实测包位置发生变化，reduced-motion 时轨道和节点动画均停止。
- initialize 删除 #workbenchNav .aside-foot（来源说明和重复新建入口），保留顶部创建任务能力。
- 1101px及以上折叠侧栏，12个导航图标与展开按钮统一44px框，13个SVG中心x均41.5px，误差0。增加导航可访问名称。
- 复用原来源内嵌Google产品素材，恢复首页产品标识，四个快速请求来源添加匹配标识。没有运行时CDN；点击标识仍能切换原复选框。320px小屏单独安排标识和标题，320/390px无横向溢出。

构建/typecheck/6项业务回归/git diff --check通过，未新建搜索、未重启服务或变更数据库。证据 output/ui-followup-20261112/，可审核完整页 designs/searchflow-ai-native-20261112/review-fixes.html。

部署阻塞：两机SSH在握手前关闭连接。曾成功上传候选与发布脚本到应用机/tmp，但执行发布的SSH随后断开；没有成功替换生产页面。一次尝试通过root重写/tmp中的用户所有文件被系统protected_regular拒绝，也未进入发布代码。

恢复时先重新核实生产SHA，再上传本地最新候选至/tmp/searchflow-review-fixes-20261112.html及output/ui-followup-20261112/publish.py，执行sudo python3。脚本CAS要求上版c279，剥离呈现层后业务HTML字节一致，首次备份路径state/ui-review-fixes-20261112/backup/index.html不得覆盖。若生产已经更新，先评估新变更，不绕过断言。

浏览器工作区275在错误停止时保留以便恢复（ego指南禁止错误停止时finish），不是已完成空间。线上需补验四项反馈后再完成清理与交付。

追加用户反馈：品牌区改为单行，移除“数据研究工作台”，S标识26px、名称16px；本地预览与构建通过，最新候选仍未部署，SSH握手前关闭。

最终品牌反馈：仅保留SearchFlow，移除S标识与副标题；折叠时隐藏文字，展开按钮保留。构建及DOM核验通过；尚未部署。

## 最终发布与核验

恢复已有跳板机私网通道：本机SSH到采集机，再连应用机10.0.0.7（HostKeyAlias保留公网主机身份校验）。公网SSH此前间歇关闭，未确定其服务器侧原因；没有改变SSH、防火墙或系统代理设置。通过私网完成CAS静态发布，备份state/ui-review-fixes-20261112/backup/index.html，原业务HTML字节一致，无后端重启、新搜索或数据库变更。

正式DOM核验品牌仅SearchFlow，S标识/副标题/侧栏底部已移除；13个折叠图标中心一致；数据包实测移动；四个Google来源标识正常加载。320/390px抽屉和API页无横向溢出。正式HTTP200且响应哈希与候选一致，无捕获脚本错误。证据output/ui-followup-20261112/production-verification.json及production-home.png。

已完成工作区275的finish，保留正式首页供用户查看；本任务临时远程文件、SSH主连接和本地预览服务均已清理。
