'use strict';

const byId = id => document.getElementById(id);
const zh = {
  logout:'退出', heading:'搜索并导出数据', intro:'发起搜索、查看结果，并下载 CSV 或 JSONL。',
  signin:'使用 Google 登录', limits:'免费额度为每日 5 次新任务，每月 30 次成功查询，均按 UTC 计算。',
  source:'数据源', language:'结果语言', english:'英语', chinese:'简体中文', mode:'搜索方式',
  cancel:'取消搜索', retry:'查看进度', keyword:'关键词', nearby:'附近商家', city:'城市', radius:'半径', search:'搜索',
  results:'查询结果', csv:'下载 CSV', jsonl:'下载 JSONL', json:'查看 JSON', privacy:'隐私政策', terms:'使用条款'
};
const copy = Object.fromEntries([...document.querySelectorAll('[data-text]')].map(el => [el.dataset.text, el.textContent]));
let locale = 'en';
try { if (localStorage.getItem('searchflow.language') === 'zh-CN') locale = 'zh-CN'; } catch {}
let account = null;
let active = null;
let submitting = false;
let timer;
let pending;
let cancelling = false;
let consecutiveReadFailures = 0;
let pollVersion = 0;
const text = (en, cn) => locale === 'en' ? en : cn;
const isChineseNews = () => byId('source').value === 'google_news' && byId('resultLanguage').value === 'zh';
const isMaps = () => byId('source').value === 'google_maps';
const url = job => '/api/account/' + (job.maps ? 'maps/jobs/' : 'search/') + job.id;

function quota() {
  if (!account) return;
  const {daily_remaining, daily_limit, monthly_remaining, monthly_limit} = account;
  byId('usage').textContent = text(
    'Remaining: ' + daily_remaining + '/' + daily_limit + ' today · ' + monthly_remaining + '/' + monthly_limit + ' this month (UTC)',
    '剩余额度：今日 ' + daily_remaining + '/' + daily_limit + ' 次 · 本月 ' + monthly_remaining + '/' + monthly_limit + ' 次（UTC）'
  );
}

function fields() {
  const nearby = isMaps() && byId('mapMode').value === 'nearby';
  byId('searchScope').hidden = !isChineseNews();
  byId('searchScope').textContent = text('Chinese news edition: Singapore','中文新闻使用新加坡版');
  byId('mapFields').hidden = !isMaps();
  byId('queryLabel').textContent = nearby ? text('Business type','商家类型') : text('Keywords','关键词');
}

function language() {
  document.documentElement.lang = locale;
  const button = byId('language');
  button.firstElementChild.textContent = text('中文','EN');
  button.firstElementChild.lang = text('zh-CN','en');
  button.setAttribute('aria-label', text('中文, switch to Simplified Chinese','EN，切换为英文'));
  for (const element of document.querySelectorAll('[data-text]')) {
    element.textContent = (locale === 'en' ? copy : zh)[element.dataset.text];
  }
  quota();
  fields();
}

function notice(message, error = false) {
  byId('notice').textContent = message;
  byId('notice').dataset.error = String(error);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  let response, data;
  try {
    response = await fetch(path, {...options, signal:controller.signal});
    data = await response.json().catch(() => ({}));
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    const error = new Error(data.error || text('The request failed.','请求失败。'));
    error.status = response.status;
    error.code = data.code;
    throw error;
  }
  if (!Object.keys(data).length) throw new Error(text('The response could not be read.','暂时无法读取响应。'));
  return data;
}

function controls() {
  byId('workspace').hidden = !account;
  byId('welcome').hidden = Boolean(account);
  byId('logout').hidden = !account;
  byId('submit').disabled = submitting || Boolean(active) || !account;
  byId('submit').textContent = submitting ? text('Starting…','正在提交…') : text('Search','搜索');
  byId('cancel').hidden = !active || active.maps;
  byId('cancel').disabled = cancelling;
  byId('retry').hidden = !active || consecutiveReadFailures < 3;
}

async function refreshAccount() {
  try {
    account = await api('/api/account/usage');
  } catch (error) {
    if (error.status === 401) account = null;
    else if (!account) notice(text('Account details are temporarily unavailable. Reload to retry.','暂时无法读取账号，请刷新重试。'), true);
  }
  quota();
  controls();
}

function renderResult(job, data) {
  byId('resultContext').textContent = job.query || '';
  byId('resultContext').hidden = !job.query;
  const rows = byId('rows');
  rows.replaceChildren();
  if (job.maps) {
    for (const place of data.result_cache.places) {
      const row = document.createElement('li');
      const name = document.createElement('strong');
      name.textContent = place.name || text('Unnamed business','商家名称未知');
      const details = document.createElement('p');
      details.textContent = [place.formatted_address, place.national_phone_number,
        place.rating == null ? '' : place.rating + ' ★'].filter(Boolean).join(' · ');
      row.append(name, details);
      rows.append(row);
    }
    if (!rows.children.length) {
      const empty = document.createElement('li');
      empty.textContent = text('No matching businesses.','没有匹配的商家。');
      rows.append(empty);
    }
  } else {
    const occurrences = data.page_observations?.length ?
      data.page_observations.flatMap(page => page.occurrences || []) : (data.occurrences || []);
    for (const result of [...occurrences, ...(data.topics || [])]) {
      const row = document.createElement('li');
      const href = result.url?.original_url || result.link;
      const name = document.createElement(/^https?:\/\//i.test(href || '') ? 'a' : 'strong');
      if (name.tagName === 'A') {
        name.href = href;
        name.target = '_blank';
        name.rel = 'noopener noreferrer';
      }
      name.textContent = result.title || result.keyword || result.query || href || text('Result','结果');
      const details = document.createElement('p');
      details.textContent = result.snippet || result.description || result.approx_traffic || '';
      row.append(name, details);
      rows.append(row);
    }
  }
  if (!rows.children.length) {
    const row = document.createElement('li');
    row.textContent = text('No matches. Try a different search term.','没有匹配结果，可以换个关键词再试。');
    rows.append(row);
  }
  byId('raw').hidden = job.maps;
  byId('payload').textContent = job.maps ? '' : JSON.stringify(data, null, 2);
  byId('csv').href = url(job) + '/export?format=csv';
  byId('jsonl').href = url(job) + '/export?format=jsonl';
  byId('csv').download = 'searchflow-' + job.id + '.csv';
  byId('jsonl').download = 'searchflow-' + job.id + '.jsonl';
  byId('downloads').hidden = false;
  byId('output').hidden = false;
}

function saveJob(key, job) {
  try {
    if (job) sessionStorage.setItem(key, JSON.stringify(job));
    else sessionStorage.removeItem(key);
  } catch {}
}

function savedJob(key) {
  try {
    const job = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (typeof job?.maps === 'boolean' && /^[0-9a-f-]{36}$/.test(job.id)) return job;
  } catch {}
  return null;
}

const save = job => saveJob('sfPublicJob', job);

async function poll(job) {
  clearTimeout(timer);
  const version = ++pollVersion;
  const current = () => active === job && pollVersion === version;
  try {
    const data = await api(url(job));
    if (!current()) return;
    if (data.status === 'succeeded') {
      const result = await api(url(job) + (job.maps ? '/results' : '/data'));
      if (!current()) return;
      renderResult(job, result);
      saveJob('sfPublicResult', job);
      notice(text('Search complete.','查询完成。'));
    } else if (data.status === 'cancelled') {
      notice(text('Search cancelled.','查询已取消。'));
    } else if (data.status === 'failed') {
      notice(text('Search could not finish. Try again or change the search.','查询未完成，可以重试或调整搜索条件。'), true);
    } else {
      consecutiveReadFailures = 0;
      controls();
      notice(data.status === 'queued' ? text('Waiting for an available slot…','正在排队…') :
        text('Searching…','正在查询…'));
      timer = setTimeout(() => poll(job), 3000);
      return;
    }
  } catch (error) {
    if (!current()) return;
    if (![401,403,404,410].includes(error.status)) {
      consecutiveReadFailures += 1;
      controls();
      notice(text('Connection interrupted. Checking the same search again…','连接暂时中断，正在重新查看同一个查询。'));
      timer = setTimeout(() => poll(job), Math.min(30000, 3000 * 2 ** consecutiveReadFailures));
      return;
    }
    save(null);
    notice(error.status === 401
      ? text('Sign in again to continue.','请重新登录后继续。')
      : text('This search is no longer available. Start a new search.','这次查询已无法读取，可以开始新查询。'), true);
  }
  active = null;
  save(null);
  controls();
  await refreshAccount();
}

byId('cancel').onclick = async () => {
  const job = active;
  if (!job || job.maps || cancelling) return;
  cancelling = true;
  controls();
  notice(text('Cancelling search…','正在取消查询…'));
  try {
    await api(url(job) + '/cancel', {method:'POST'});
    if (active === job) await poll(job);
  } catch {
    if (active === job) notice(text('Cancellation was not confirmed. Checking the search…','暂未确认取消，正在查看查询状态。'), true);
  } finally {
    cancelling = false;
    controls();
  }
};
byId('retry').onclick = () => {
  if (!active) return;
  consecutiveReadFailures = 0;
  controls();
  poll(active);
};

byId('language').onclick = () => {
  locale = text('zh-CN','en');
  try { localStorage.setItem('searchflow.language',locale); } catch {}
  language();
  controls();
};
byId('source').onchange = fields;
byId('resultLanguage').onchange = fields;
byId('mapMode').onchange = fields;
byId('logout').onsubmit = () => { clearTimeout(timer); save(null); saveJob('sfPublicResult', null); };
byId('search').onsubmit = async event => {
  event.preventDefault();
  if (!account || submitting || active) return;
  const query = byId('query').value.trim();
  if (!query) {
    notice(text('Enter a search term.','请输入搜索词。'), true);
    byId('query').focus();
    return;
  }
  const maps = isMaps();
  const payload = {query, language:byId('resultLanguage').value};
  const address = maps ? '/api/account/maps/jobs' : '/api/account/search';
  if (maps) {
    const city = byId('city').selectedOptions[0];
    Object.assign(payload, {mode:byId('mapMode').value, city:city.value, region:city.dataset.region,
      radius_m:Number(byId('radius').value)});
    if (payload.mode === 'nearby') Object.assign(payload,{query:'',included_type:query});
  } else {
    Object.assign(payload, {source:byId('source').value,region:isChineseNews() ? 'SG' : 'US'});
  }
  const body = JSON.stringify(payload);
  if (pending?.body !== body || pending?.address !== address) pending = {address,body,id:crypto.randomUUID()};
  submitting = true;
  controls();
  notice(text('Starting search…','正在提交查询…'));
  try {
    const job = await api(address,{method:'POST',body,headers:{'Content-Type':'application/json','Idempotency-Key':pending.id}});
    pending = null;
    active = {maps,id:job.id,query};
    save(active);
    controls();
    await poll(active);
  } catch (error) {
    notice(error.message, true);
    await refreshAccount();
  } finally {
    submitting = false;
    controls();
  }
};

language();
if (new URLSearchParams(location.search).get('login') === 'failed') {
  notice(text('Sign-in could not be completed. Please try again.','登录未完成，请重试。'), true);
}
refreshAccount().then(async () => {
  if (!account) return;
  active = savedJob('sfPublicJob');
  controls();
  const last = savedJob('sfPublicResult');
  if (last && last.id !== active?.id) {
    try {
      const previous = await api(url(last) + (last.maps ? '/results' : '/data'));
      if (byId('output').hidden) renderResult(last, previous);
    } catch (error) {
      if ([401,403,404,410].includes(error.status)) saveJob('sfPublicResult', null);
    }
  }
  if (active) poll(active);
});
