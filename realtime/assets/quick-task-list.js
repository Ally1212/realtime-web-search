(() => {
  'use strict';
  const key = 'searchflowQuickTasksV1';
  const pageSize = 8;
  const terminal = new Set(['succeeded', 'failed', 'cancelled', 'stopped']);
  const sources = new Set(['google_web', 'google_news', 'google_trends', 'google_images']);
  const rows = $('quickTaskRows');
  const note = $('quickTaskNotice');
  const refresh = $('quickTaskRefresh');
  const result = $('quickTaskResult');
  const resultStatus = $('quickTaskResultStatus');
  const downloads = $('quickTaskDownloads');
  const jobs = new Map();
  const elements = new Map();
  const attemptedMigration = new Set();
  let visibleCount = pageSize;
  let checking = false;
  let syncing;
  let lastSync = 0;
  let selected = null;
  let sequence = 0;

  function save() {
    try { sessionStorage.setItem(key, JSON.stringify([...jobs.values()].filter(job => job.token))); } catch {}
  }

  function merge(entry, fromBrowserHistory = false) {
    const {id, token} = entry;
    const {query, source} = entry.params || {};
    if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id || '')
        || (!fromBrowserHistory && !/^[0-9a-f]{64}$/i.test(token || ''))
        || !sources.has(source) || typeof query !== 'string') return;
    const old = jobs.get(id);
    const server = entry.server || old?.server || {};
    if (old && terminal.has(old.status) && !terminal.has(entry.status)) return;
    jobs.set(id, {id, token:token || old?.token, params:{query,source}, status:entry.status || old?.status || 'queued',
      server:{started_at:server.started_at || old?.server.started_at,
        response:{stage:server.response?.stage || old?.server.response.stage}, progress:{
        elapsed_seconds:server.progress?.elapsed_seconds ?? old?.server.progress.elapsed_seconds,
        web_pages:server.progress?.web_pages || old?.server.progress.web_pages,
      }},
    });
    if (jobs.size > 40) jobs.delete(jobs.keys().next().value);
  }

  function readRun() {
    if (!Array.isArray(quickRun?.entries)) return;
    for (const entry of quickRun.entries) merge(entry);
    save();
    if (location.hash === '#tasks') render();
  }

  function currentJobs() {
    return [...jobs.values()].reverse().slice(0, visibleCount);
  }

  function auth(job) {
    return job.token ? {'X-Quick-Token':job.token} : {};
  }

  async function syncBrowser(force = false) {
    if (syncing) return syncing;
    if (!force && Date.now() - lastSync < 8000) return true;
    syncing = (async () => {
      try {
        let {tasks} = await qApi('/browser-tasks');
        const present = new Set(tasks.map(job => job.id));
        const capacity = Math.max(0, Math.min(8, 20 - tasks.length));
        const receipts = capacity ? [...jobs.values()].filter(job => job.token && !present.has(job.id)
          && !attemptedMigration.has(job.id)).reverse().slice(0, capacity).map(({id,token}) => ({id,token})) : [];
        if (receipts.length) {
          const answer = await qApi('/browser-tasks', {receipts});
          for (const {id} of receipts) attemptedMigration.add(id);
          if (answer.remembered.length) ({tasks} = await qApi('/browser-tasks'));
        }
        const visible = new Set(tasks.map(job => job.id));
        for (const [id, job] of jobs) if (!job.token && !visible.has(id)) jobs.delete(id);
        for (const job of [...tasks].reverse()) merge(job, true);
        lastSync = Date.now();
        return true;
      } catch {
        lastSync = Date.now();
        return false;
      }
    })();
    try { return await syncing; } finally { syncing = null; }
  }

  function createRow(id) {
    const row = document.createElement('li');
    row.dataset.quickTask = id;
    row.innerHTML = '<div><strong></strong><span class="quick-task-state"></span><small class="help"></small><progress hidden aria-label="已尝试的网页数"></progress></div>'
      + '<button type="button" class="secondary" hidden>查看结果</button>';
    elements.set(id, row);
    return row;
  }

  function stage(job) {
    if (typeof qLiveStage === 'function') return qLiveStage(job)[0];
    return ({queued:'正在排队', running:'正在采集', succeeded:'结果已就绪',
      failed:'采集失败', cancelled:'已停止', stopped:'已停止'})[job.status] || '已受理';
  }

  function duration(seconds) {
    if (typeof qDuration === 'function') return qDuration(seconds);
    const total = Math.max(0, Math.round(seconds));
    return total < 60 ? total + ' 秒' : Math.floor(total / 60) + ' 分 ' + (total % 60) + ' 秒';
  }

  function render() {
    const visible = currentJobs();
    $('quickTaskEmpty').hidden = visible.length > 0;
    $('quickTaskMore').hidden = jobs.size <= visibleCount;
    refresh.disabled = checking;
    for (const [id, row] of elements) {
      if (!visible.some(job => job.id === id)) {
        row.remove();
        elements.delete(id);
      }
    }
    for (const [index, job] of visible.entries()) {
      const row = elements.get(job.id) || createRow(job.id);
      if (rows.children[index] !== row) rows.insertBefore(row, rows.children[index] || null);
      const [title, state, meta, bar] = row.firstElementChild.children;
      title.textContent = (quickLabels[job.params.source] || job.params.source) + ' · ' + job.params.query;
      state.textContent = stage(job);
      const {elapsed_seconds, web_pages} = job.server.progress;
      const hasPages = Number.isInteger(web_pages?.planned) && web_pages.planned > 0
        && Number.isInteger(web_pages.attempted) && Number.isInteger(web_pages.completed);
      meta.textContent = [
        Number.isFinite(elapsed_seconds) ? '用时 ' + duration(elapsed_seconds) : '',
        hasPages ? `已尝试 ${web_pages.attempted} / ${web_pages.planned} 页 · ${web_pages.completed} 页返回结果或确认无结果` : '',
      ].filter(Boolean).join(' · ');
      bar.hidden = !hasPages;
      if (hasPages) {
        bar.max = web_pages.planned;
        bar.value = web_pages.attempted;
      }
      row.lastElementChild.hidden = job.status !== 'succeeded';
    }
  }

  async function check(manual = false) {
    if (checking || location.hash !== '#tasks' || document.hidden) return;
    checking = true;
    refresh.disabled = true;
    if (manual) note.textContent = '正在读取任务进度…';
    const historyAvailable = await syncBrowser(manual);
    const candidates = currentJobs().filter(job => manual || !terminal.has(job.status));
    let failed = historyAvailable ? 0 : 1;
    let completed = 0;
    for (let i = 0; i < candidates.length; i += 4) {
      if (location.hash !== '#tasks') break;
      await Promise.all(candidates.slice(i, i + 4).map(async job => {
        try {
          const server = await qApi('/' + job.id, undefined, auth(job));
          if (!jobs.has(job.id)) return;
          if (!terminal.has(jobs.get(job.id).status) && server.status === 'succeeded') completed++;
          merge({...job, status:server.status, server}, !job.token);
        } catch {
          failed++;
        }
      }));
    }
    checking = false;
    save();
    render();
    if (failed) note.textContent = '部分进度暂时无法读取，请刷新重试。';
    else if (completed) note.textContent = `${completed} 笔快速搜索已完成，可以查看结果。`;
    else if (manual) note.textContent = '已刷新任务进度。';
  }

  async function viewResult(job) {
    const request = ++sequence;
    selected = null;
    result.hidden = false;
    downloads.hidden = true;
    $('quickTaskResultTitle').textContent = job.params.query;
    $('quickTaskResultRows').replaceChildren();
    resultStatus.textContent = '正在读取已保存结果…';
    $('quickTaskResultTitle').focus();
    try {
      const data = await qApi('/' + job.id + '/data', undefined, auth(job));
      if (request !== sequence) return;
      const items = data.occurrences || [];
      const topics = data.topics || [];
      const results = [...items.map(item => ({title:item.title || item.source_fields?.title, url:item.url?.original_url})),
        ...topics.map(topic => ({title:topic.topic || topic.label, url:null}))];
      $('quickTaskResultRows').innerHTML = results.slice(0, 5).map(item =>
        '<li>' + (qLink(item.url, item.title) || esc(item.title || '标题未提供')) + '</li>').join('');
      resultStatus.textContent = `${items.length} 条结果、${topics.length} 个话题${results.length > 5 ? ' · 预览前 5 条，下载包含完整结果' : ''}`;
      selected = job;
      for (const button of downloads.querySelectorAll('button')) button.disabled = false;
      downloads.hidden = false;
    } catch (error) {
      if (request === sequence) resultStatus.textContent = error.code === 'not_found'
        ? '当前任务回执已不可用，或结果已过期。' : '暂时无法读取结果，请重试。';
    }
  }

  try {
    const stored = JSON.parse(sessionStorage.getItem(key) || '[]');
    if (Array.isArray(stored)) for (const job of stored.slice(-40)) merge(job);
  } catch {}
  const saveRun = qSave;
  qSave = function () {
    saveRun();
    readRun();
  };
  refresh.addEventListener('click', () => check(true));
  $('quickTaskMore').addEventListener('click', () => { visibleCount += pageSize; render(); check(); });
  rows.addEventListener('click', event => {
    const row = event.target.closest('button')?.closest('[data-quick-task]');
    const job = row && jobs.get(row.dataset.quickTask);
    if (job?.status === 'succeeded') viewResult(job);
  });
  downloads.addEventListener('click', async event => {
    const button = event.target.closest('[data-quick-task-format]');
    const job = selected;
    if (!button || !job) return;
    button.disabled = true;
    const {quickTaskFormat:format} = button.dataset;
    try {
      const response = await fetch(`/api/quick-request/${job.id}/export?format=${format}`, {
        headers:{'X-SearchFlow-Quick':'1', ...auth(job)},
        signal:AbortSignal.timeout(30000),
      });
      if (!response.ok) throw new Error('download unavailable');
      const blob = await response.blob();
      saveBlob(blob, `searchflow-${job.id}.${format}`);
      if (selected === job) resultStatus.textContent = '已下载完整结果。';
    } catch {
      if (selected === job) resultStatus.textContent = '下载失败，请重试。';
    } finally {
      button.disabled = false;
    }
  });

  function showTasks() {
    if (location.hash !== '#tasks') return;
    readRun();
    render();
    check();
  }
  window.addEventListener('hashchange', showTasks);
  document.addEventListener('visibilitychange', showTasks);
  readRun();
  if (location.hash !== '#tasks' && jobs.size) syncBrowser(true);
  showTasks();
  setInterval(check, 10000);
})();
