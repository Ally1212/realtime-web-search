# 实时网页搜索爬虫

输入关键词后实时调用免费搜索引擎发现 URL，再由本机并发访问原网页、提取当前正文并写入 OpenSearch。项目不读取 Common Crawl，也不使用付费 API。

```bash
docker compose up -d --build
```

打开 <http://localhost:8091>，输入关键词并点击“实时抓取”。

## 数据口径

- “搜索发现”是一条搜索结果 URL。
- “实时成功”表示本机刚刚访问原 URL 并成功提取正文。
- “索引网页总数”按规范化 URL 去重；同一 URL 再抓会更新，不重复累计。
- 搜索引擎可能限流，网页可能受 `robots.txt`、登录、JavaScript 或反爬限制。
- 本爬虫只请求公网 HTTP/HTTPS 地址，遵守 `robots.txt`，限制单页下载大小。
