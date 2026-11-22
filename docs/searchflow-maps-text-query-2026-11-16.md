# Maps 文字查询与自动定位上线说明

Maps 现在支持从 Whale 直接提交 `coffee in Seattle` 这类带城市的文字查询，无需调用方手写坐标、半径、地区或语言。SearchFlow 网站 Maps API 也使用同一条采集与中台存取路径。

## 现在怎么调用

使用原有 Consumer Bearer 和新的 Idempotency-Key，向 Whale `POST /v1/live-queries` 提交：

```json
{
  "dataset_id": "google_maps_raw",
  "resource_kind": "content",
  "source_platforms": ["google_maps"],
  "query": "coffee in Seattle",
  "max_items": 2
}
```

该请求会定位西雅图，保留原查询词搜索，最多交付两家商家。未传语言时使用英语；默认搜索范围为定位中心周围5000米。地区参数可作为定位国家提示，但不需要调用方为了运行程序补造地区。没有定位信息的 `coffee` 或 `near me` 无法确定位置，会明确提示补充城市；重名城市需要补充州或国家。

普通文字查询的 `max_items` 限制商家数，可输入1–100，当前快速采集上限21家；可能少于请求数量，不补造结果。中台仍保存一份查询快照，快照内包含商家列表，因此平台文档数不等于商家数。原来带位置conditions、max_items=1的一份快照协议继续兼容，不会因此只保留一家商家。

网站 `/v1/google/local/jobs` 或网站内部 `/api/maps/jobs` 可提交：

```json
{"mode":"text","query":"coffee in Seattle","max_places":2}
```

文字模式也可提供 `query=coffee`、`city=Seattle`。原预设城市及精确坐标/半径条件继续使用原路径；附近类型模式仍需预设城市或明确坐标。中文支持 `西雅图的咖啡店` 这类明确写出地点的形式；不宣称支持任意自然语言指令解析。

## 修复的根因

原适配器把所有Maps任务强制转换为坐标附近搜索。现用gosom v1.18.2快速模式本身也要求geo坐标，单纯删除接口校验无法解决。现新增位置解析模块，使用Nominatim返回的真实OSM城市坐标，而不是硬编码Seattle或猜测坐标。调用前按来源、数据集、live_query类型、能力和conditions校验。

位置解析由单个Maps运行器执行，网站请求不在API进程同步查询地理编码。成功坐标、显示名称、来源、署名与采集范围持久保存，恢复使用已保存参数；不会因重启重新猜城市。使用共享SQLite缓存及进程间锁限制位置解析频率不超过每秒一次，成功结果缓存30天。地理编码不可用时延后重试；无法确定或有歧义时明确失败，不返回服务器附近的商家。

完整原查询、位置解析信息、原始采集响应、交付商家列表写入同一中台快照后计算hash；按原事件键提交realtime、等待accepted/duplicate后完成任务。Consumer按对应version读回实际载荷并严格校验。网站结果/JSONL提供location_resolution和search_area，保留OpenStreetMap署名和实际中心点/半径；CSV商家内容与读取结果保持一致。

## 正式验证

| 入口 | 查询 | 返回 | 验证 |
|---|---|---|---|
| Whale直接派单 | coffee in Seattle，max_items=2，无坐标/地区/语言 | 2家西雅图咖啡店 | succeeded、完整固定版本载荷及SHA256一致 |
| Whale直接派单 | dentists in Kyoto，max_items=3，无坐标 | 3家京都牙医 | succeeded、完整固定版本载荷及SHA256一致 |
| 网站Maps API | coffee in Seattle，max_places=2，无坐标 | 2家 | 中台固定版本读回、CSV/JSONL深比较、同幂等键复用通过 |

对应Whale查询为 `lq_5f356647a153e791c233662a3ed2262d` 和 `lq_350551971ee43d6fe24ead63a3eb5799`。京都读取结果接口遇429后复用原查询退避读回通过，没有重新采集。

相关69项隔离PostgreSQL回归通过、无跳过；后续结果中增加定位/搜索范围字段的36项相关回归通过。覆盖原始失败请求、真实商家数量限制、坐标半径严格校验、位置为空/歧义/非法坐标/服务不可用、缓存、取消、重启、回执和网站任务归属。属于小范围功能验证，不是长期SLA或全世界城市覆盖率验证。

## 发布与回退

正式部署沿用原冻结运行目录，仅更新Maps适配、提供方、共享参数校验、客户端条件校验及OpenAPI说明；新增maps_location.py。原SERP采集服务、代理、PostgreSQL、静态页面未替换，持续采集、正文及旧Whale上传/领取开关未开启。独立实时运行器及Web/PRD已按代码依赖重启，未运行历史数据库迁移。

生产备份：`/home/azureuser/projects/google-workbench-backend/state/maps-text-20261116/backup/`；结果元数据补充前的文件另外保存在backup-before-provenance。回退先暂停新增请求并排空Maps在途任务，再停相关服务，恢复backup/runtime与backup/web中的原模块，保留业务数据及直派任务历史后重启。新模块无数据库迁移。

证据在本地 `state/maps-text-20261116/`，包含候选、基线、部署记录、final-regression.log、provenance-regression.log、live-probe-final.log和website-probe-final.log。位置解析新增依赖公共地理编码服务，限流/暂时故障可延迟查询；明确坐标查询不依赖该服务。

最终网站验收任务 `2c2605c1-efd0-435d-b8b8-5b4d5b5a66de` 返回2家，实际定位Seattle、radius_m=5000，结果中已可读取location_resolution/search_area；CSV/JSONL深比较与幂等复用通过。此前任务 `6ca6715c-d30d-4e94-8bed-2b85d3778114` 为补充公开定位字段前的成功样本。独立本地测试PostgreSQL容器已删除。
