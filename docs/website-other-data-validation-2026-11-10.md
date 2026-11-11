# 网站其他真实数据验证（2026-11-10）

本轮使用网站已有公开数据，只读调用正式网站接口；没有新搜索、上传、部署或开关修改。通过 ego-browser 独立空间 125 操作，完成后已关闭并确认移除。

## 结果

每类选择最新两次成功公开请求，共六次：

| 来源 | 请求 ID | 结果数 | 话题数 | CSV 数据行数 |
| --- | --- | ---: | ---: | ---: |
| News | 64c7cab5-70bb-4191-abaf-e66efa204639 | 55 | 0 | 56 |
| News | 506ebeb2-e91b-4da0-a157-4cde99fd674e | 30 | 0 | 31 |
| Trends | 2f063f32-fe5d-400a-b24d-7a4d20a7107a | 30 | 10 | 41 |
| Trends | 6bf59424-7de6-4282-8b2b-d7686d63d258 | 30 | 10 | 41 |
| Images | 651c7e2f-52b0-4c68-9698-89ab708f976f | 20 | 0 | 21 |
| Images | 7e5d3f65-5380-4205-8a60-ca7e20b1bb6b | 19 | 0 | 20 |

三类列表，以及六个请求的原始响应、JSONL、CSV 均返回 HTTP 200。CSV 每份 74 列，另含一条 request 记录，所以数据行数大于结果数。

逐请求验证通过：列表对象与 JSONL 完全相等；保存响应经过既有 public_fields 过滤后的摘要与 source_response_hash 一致；CSV record_json 按顺序完整还原请求、结果、话题及特征；每条结果 source_fields 与其摘要一致。Trends 六十条报道均关联到本请求存在的话题；Images 三十九条均保留图片 URL、来源页面 URL、宽度和高度。

## 范围与限制

这是网站保存数据到读取、导出的真实样本验证，不是全库扫描，也没有验证这些请求已同步到 Marsyoo。网站接口仍声明 saved_serp_requests_not_whale_readback。Trends 样本为热门话题与报道，不代表历史数值曲线；Images 保留上游图片链接和尺寸，本轮不下载图片文件或独立核实上游尺寸。

证据：`state/website-other-data-validation-20261110/verification.json` 及同目录逐请求响应和导出，目录 700、文件 600。离线 CSV 校验明确提高字段上限到 64 MiB，以读取含完整请求 JSON 的单元格；未修改线上导出。
