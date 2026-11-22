> 最新要求已覆盖本文早期方案：复用中台google_search与assets，无需独立google_images平台。真实历史图片样本存取已通过。最新实现、统一任务分派和上线边界见 [现有平台适配记录](searchflow-images-reuse-existing-platform-2026-11-13.md)。

# Images 中台内部适配进展

用户确认 social_media_raw + google_images；新增内部SearchMiddlePlatformClient来源映射、20条请求上限、Collector任务转换、LiveRunner图片分支与独立锁14。图片URL按已有image-exact-url-1语义保留完整参数；所选图片写入完整platform_payload，固定版本/hash校验复用原流程。Product公开入口仍明确拒绝，因为正式中台OpenAPI未提供google_images；未发布、未配置权限、未真实写入。

新增 tests/test_middle_images.py 覆盖来源/限制、精确URL与字段保留、固定版本防篡改。提取selected_observation后，回归发现Trends跨主题同URL重复进入载荷；已补全局去重并通过既有复现测试。最新针对性17项和2 subtests通过，compileall通过。

首次广泛相关回归40 passed/3 skipped/65 subtests及1个上述Trends失败；修复后的最终相关回归见 output/images-middle-20261113/targeted-final.log。首次全库898 passed/618 skipped/642 subtests，1个上述已修复Trends失败，7个tests/test_google_legacy.py::LegacyPreviewTests初始化错误均为缺TEST_DATABASE_URL；不能宣称全库通过。全库日志保留于output/images-middle-20261113/full-regression.log。第一次pytest直接可执行文件调用出现tests包导入错误，改为项目根目录python -m pytest后正常收集。

同事交接：docs/给中台同事-Google图片来源适配交接-2026-11-13.md。用户明确由其他同事负责中台，等待其补充契约后再真实联调。当前代码未提交，工作树包含大量先前及并行任务修改，不应整仓发布。本任务ego空间331已finish，未操作其他发布任务空间329。

最终相关回归执行结束且退出码0；具体通过、跳过及subtest数量以targeted-final.log末行原始结果为准。git diff --check通过。


## 复用正式assets契约

用户确认复用既有图片能力。link_message新增标准assets数组（media_type=image/role=media），url为精确图片地址，preview_url为有效公开缩略图，width/height仅已知正整数；不伪造MIME、不下载文件、不增加cover副本。消息提供assets能力，图片Collector注册/领取/任务契约使用同一能力列表，其他来源不增加assets。

标准assets同时放入固定版本platform_payload并参与摘要，测试校验尺寸篡改被拒绝、CSV/JSONL保持图片字段。注意固定载荷证明不等于已验证中台metadata.assets投影；真实中台尚缺google_images来源，未做真实保存/读回与部署。相关日志assets-targeted.log、全库assets-full.log。本轮未使用浏览器、未修改线上配置。

本轮最终验证：相关39 passed/60 subtests，64.43秒；全库903 passed/618 skipped/642 subtests，7 errors全部为LegacyPreviewTests缺TEST_DATABASE_URL（93.20秒），无测试断言失败；compileall/git diff --check通过。独立只读审查未发现重要问题。公开入口未就绪行为由现有middle_search_sources测试保留。代码未提交/未发布。
