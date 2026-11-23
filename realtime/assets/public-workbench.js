'use strict';

const byId = id => document.getElementById(id);
const zh = {
  logout:'退出', heading:'搜索并导出数据', intro:'发起搜索、查看结果，并下载 CSV 或 JSONL。',
  signin:'使用 Google 登录', limits:'免费额度为每日 5 次新任务，每月 30 次成功查询，均按 UTC 计算。',
  source:'数据源', language:'结果语言', english:'英语', chinese:'简体中文', mode:'搜索方式',
  keyword:'关键词', nearby:'附近商家', city:'城市', radius:'半径', search:'搜索',
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
const text = (en, cn) => locale === 'en' ? en : cn;
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
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || text('The request failed.','请求失败。'));
    error.status = response.status;
    throw error;
  }
  return data;
}

function controls() {
  byId('workspace').hidden = !account;
  byId('welcome').hidden = Boolean(account);
  byId('logout').hidden = !account;
  byId('submit').disabled = submitting || Boolean(active) || !account;
}

async function refreshAccount() {
  try {
    account = await api('/api/account/usage');
  } catch (error) {
    account = null;
    if (error.status !== 401) notice(error.message, true);
  }
  quota();
  controls();
}

function renderResult(job, data) {
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
    for (const result of occurrences) {
      const row = document.createElement('li');
      const name = document.createElement('strong');
      name.textContent = result.title || result.url?.original_url || text('Result','结果');
      const details = document.createElement('p');
      details.textContent = result.snippet || result.description || '';
      row.append(name, details);
      rows.append(row);
    }
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

function save(job) { try { job ? sessionStorage.setItem('sfPublicJob', JSON.stringify(job)) : sessionStorage.removeItem('sfPublicJob'); } catch {} }

async function poll(job) {
  try {
    const data = await api(url(job));
    if (data.status === 'succeeded') {
      renderResult(job, await api(url(job) + (job.maps ? '/results' : '/data')));
      notice(text('Search complete.','查询完成。'));
    } else if (data.status === 'failed' || data.status === 'cancelled') {
      notice(text('Search ended: ','查询结束：') + (data.error_code || data.status), true);
    } else {
      notice(data.status === 'queued' ? text('Waiting for an available slot…','正在排队…') :
        text('Searching…','正在查询…'));
      timer = setTimeout(() => poll(job), 3000);
      return;
    }
    active = null;
    await refreshAccount();
  } catch (error) {
    active = null;
    if ([401,404,410].includes(error.status)) save(null);
    notice(error.message, true);
    await refreshAccount();
  }
}

byId('language').onclick = () => {
  locale = text('zh-CN','en');
  try { localStorage.setItem('searchflow.language',locale); } catch {}
  language();
};
byId('source').onchange = fields;
byId('mapMode').onchange = fields;
byId('logout').onsubmit = () => { clearTimeout(timer); save(null); };
byId('search').onsubmit = async event => {
  event.preventDefault();
  if (!account || submitting || active) return;
  const query = byId('query').value.trim();
  if (!query) return;
  const maps = isMaps();
  const payload = {query, language:byId('resultLanguage').value};
  const address = maps ? '/api/account/maps/jobs' : '/api/account/search';
  if (maps) {
    const city = byId('city').selectedOptions[0];
    Object.assign(payload, {mode:byId('mapMode').value, city:city.value, region:city.dataset.region,
      radius_m:Number(byId('radius').value)});
    if (payload.mode === 'nearby') Object.assign(payload,{query:'',included_type:query});
  } else {
    Object.assign(payload, {source:byId('source').value,region:'US'});
  }
  const body = JSON.stringify(payload);
  if (pending?.body !== body || pending?.address !== address) pending = {address,body,id:crypto.randomUUID()};
  submitting = true;
  controls();
  try {
    const job = await api(address,{method:'POST',body,headers:{'Content-Type':'application/json','Idempotency-Key':pending.id}});
    pending = null;
    active = {maps,id:job.id};
    save(active);
    byId('output').hidden = true;
    await refreshAccount();
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
refreshAccount().then(() => {
  if (!account) return;
  try { active = JSON.parse(sessionStorage.getItem('sfPublicJob') || 'null'); } catch {}
  if (!active || typeof active.maps !== 'boolean' || !/^[0-9a-f-]{36}$/.test(active.id)) {
    active = null;
    return;
  }
  controls();
  poll(active);
});
