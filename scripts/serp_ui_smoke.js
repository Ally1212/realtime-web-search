async (page) => {
const checks=[];
const assert=(condition,label)=>{if(!condition)throw Error(label);checks.push(label);};
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:8094/?qa='+Date.now()+'#overview');
await page.locator('#latestRows .result').first().waitFor();
assert(await page.locator('#latestRows .result').count()>0,'真实历史数据展示');assert(await page.locator('#autoToggle,#continuousSwitch,[name=continuousSource]').count()===0,'用户网站无内部自动采集入口');
await page.screenshot({path:'output/playwright/overview-desktop.png'});
await page.locator('[data-view="tasks"]').click();
await page.locator('#taskRows tr').first().waitFor();
assert(!await page.locator('#taskForm').isVisible(),'任务列表与创建流程分离');
await page.locator('[data-view="results"]').click();
await page.locator('#resultRows article').first().waitFor();
await page.locator('#resultSource').selectOption('google_news');
await page.locator('#resultFilters button[type=submit]').click();
await page.waitForResponse(r=>r.url().includes('/api/serp/results?')&&r.url().includes('source=google_news'));
await page.locator('#resultRows article.news').first().waitFor();assert((await page.locator('#resultRows').innerText()).includes('Google News'),'News 数据筛选');
await page.locator('#resultSource').selectOption('google_trends');
await page.locator('#resultFilters button[type=submit]').click();
await page.waitForResponse(r=>r.url().includes('/api/serp/results?')&&r.url().includes('source=google_trends'));
await page.locator('#resultRows article.trends').first().waitFor();
assert((await page.locator('#resultRows').innerText()).includes('搜索量标记'),'Trends 数据展示');
const downloadWait=page.waitForEvent('download');await page.locator('#exportCsv').click();const download=await downloadWait;
assert(download.suggestedFilename().endsWith('.csv'),'真实筛选 CSV 导出');
await download.saveAs('output/playwright/export-trends.csv');
await page.locator('#resetFilters').click();await page.locator('#resultRows article.web').first().waitFor();
await page.locator('#resultRows [data-json]').first().click();await page.locator('#jsonDialog').waitFor({state:'visible'});
assert((await page.locator('#jsonSummary').innerText()).includes('采集来源'),'采集详情可读摘要');
await page.locator('#closeJson').click();
await page.locator('[data-view="whale"]').click();await page.waitForFunction(()=>!document.querySelector('#whaleConnection').textContent.includes('读取'));
assert(await page.locator('#whaleUploadForm').isVisible(),'平台同步与已有链接上传入口');
await page.locator('[data-view="settings"]').click();await page.locator('#settingsView').waitFor({state:'visible'});assert(await page.locator('#settingsForm').isVisible(),'采集设置入口');
// Isolated browser mocks exercise write flows. Local server rejects any unmatched POST.
let captured=null;let task=null;const posts=[];
await page.route('**/api/serp/tasks**',async route=>{const req=route.request();if(req.method()==='POST'){const body=req.postDataJSON();posts.push(req.url());if(new URL(req.url()).pathname==='/api/serp/tasks'){captured=body;task={id:'11111111-1111-4111-8111-111111111111',query:body.keywords[0]||'当前热门趋势',aliases:body.keywords.slice(1),sources:body.sources,status:body.start?'active':'paused',started:body.start,automatic:false,options:body.options,mode:body.mode,max_successful_searches:body.max_successful_searches,requests:0,cached:0,successful:0,new_links:0,created_at:new Date().toISOString()};}else{const op=req.url().split('/').pop();task.status={start:'active',pause:'paused',resume:'active',stop:'stopped'}[op];task.started=true;}await route.fulfill({json:{campaign_id:task.id,status:task.status}});}else await route.fulfill({json:{tasks:task?[task]:[],total:task?1:0}});});
await page.locator('a[href="#create"]').first().click();await page.locator('#wizardNext').click();await page.locator('#wizardNext').click();
assert((await page.locator('#notice').innerText()).includes('请输入至少一个关键词'),'空关键词校验');
await page.locator('#keywords').fill('solar energy\n人工智能政策\nsolar energy');await page.locator('#wizardNext').click();
assert((await page.locator('#taskReview').innerText()).includes('2 次搜索'),'多行关键词与去重预估');
await page.locator('#createDraft').click();await page.locator('#tasksView').waitFor({state:'visible'});
assert(captured.start===false&&captured.keywords.length===2,'草稿创建参数正确');
await page.locator('[data-op="start"]').click();await page.locator('[data-op="pause"]').waitFor();
await page.locator('[data-op="pause"]').click();await page.locator('[data-op="resume"]').waitFor();
await page.locator('[data-op="resume"]').click();await page.locator('[data-op="stop"]').waitFor();
await page.locator('[data-op="stop"]').click();
assert(task.status==='stopped','启动暂停继续停止操作');
await page.locator('a[href="#create"]').first().click();await page.locator('[data-template="trends"]').click();
assert(await page.locator('#keywords').isDisabled(),'纯 Trends 无需关键词');
assert((await page.locator('#sourceDescription').innerText()).includes('不提供按关键词'),'Trends 数据范围提示');
await page.locator('#wizardNext').click();await page.locator('#createStart').click();await page.locator('#tasksView').waitFor({state:'visible'});
assert(captured.sources[0]==='google_trends'&&captured.keywords.length===0&&captured.start,'纯 Trends 创建参数');
await page.unroute('**/api/serp/tasks**');
await page.setViewportSize({width:390,height:844});
for(const hash of ['overview','create','tasks','results','whale','settings']){await page.goto('http://127.0.0.1:8094/#'+hash);await page.locator('#'+hash+'View').waitFor({state:'visible'});await page.waitForFunction(()=>document.querySelector('#webRegion').value!=='');if(hash==='overview')await page.locator('#latestRows .result').first().waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'手机无页面横向溢出 '+hash);if(hash==='overview'||hash==='create')await page.screenshot({path:'output/playwright/'+hash+'-mobile.png'});}
await page.goto('http://127.0.0.1:8094/#results');await page.locator('#resultsView').waitFor({state:'visible'});await page.route('**/api/serp/export?**',async route=>{await new Promise(r=>setTimeout(r,1000));await route.fulfill({status:200,body:'mock'}).catch(()=>{});});await page.locator('#exportCsv').click();await page.locator('#cancelExport').click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('导出已取消'));assert(await page.locator('#exportCsv').isEnabled(),'大批量导出可取消并恢复按钮');await page.unroute('**/api/serp/export?**');assert(errors.length===0,'浏览器无 JavaScript 异常');
console.log(JSON.stringify({checks,isolatedPostCount:posts.length,errors},null,2));
await page.setViewportSize({width:1440,height:1000});await page.goto('http://127.0.0.1:8094/#overview');

return {checks,isolatedPostCount:posts.length,errors};

}
