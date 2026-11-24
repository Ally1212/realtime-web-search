(() => {
  'use strict';
  const demo = 'sodium ion battery research';
  const input = $('gaInput');
  const result = $('gaResult');
  const status = $('gaStatus');
  const region = $('gaRegion');
  const legacyHistory = $('researchView');
  const history = $('gaHistory');
  const advanced = $('gaReports');
  let controller;
  let applied = null;
  let researchReads = Promise.resolve();
  let lastResearchStart = 0;
  region.replaceChildren(new Option('全部', ''), ...Array.from($('analysisRegion').options)
    .filter(option => option.value).map(option => new Option(option.text, option.value)));
  history.append(legacyHistory);
  for (const [key, label] of [['analysis', 'Google 分析'], ['insights', '文本分析']]) {
    const link = document.querySelector('aside [data-view="' + key + '"]');
    if (link) link.lastChild.textContent = label;
  }

  const lower = value => (value || '').trim().toLocaleLowerCase('en');
  const link = (url, text) => {
    const valid = safeLink(url || '');
    return valid && valid !== '#'
      ? `<a href="${valid}" target="_blank" rel="noopener noreferrer">${esc(text || url)}</a>`
      : esc(text || '链接不可用');
  };
  const dateTime = new Intl.DateTimeFormat('zh-CN', {dateStyle:'medium', timeStyle:'medium', timeZone:'UTC'});
  const when = value => {
    const date = value ? new Date(value) : null;
    return date && !Number.isNaN(date.getTime()) ? esc(dateTime.format(date)) + ' UTC' : '时间未知';
  };
  const scopeText = value => [value?.country_code || value?.region, value?.language_code || value?.language, value?.device]
    .map(part => esc(part || '未知')).join(' / ');
  const evidence = (label, html) => `<details><summary>${esc(label)}</summary>${html}</details>`;
  const list = (rows, render, css = 'ga-evidence') => `<ul class="${css}">${rows.map(row => '<li>' + render(row) + '</li>').join('')}</ul>`;
  const section = (id, title, body) => `<section class="ga-section" aria-labelledby="gaTitle-${id}"><h3 id="gaTitle-${id}">${title}</h3>${body}</section>`;
  const display = (item, url = item.publisher_url || item.url) => link(url, item.title || item.topic || url);
  const observationsPath = '/api/site/research/legacy-observations';
  const requestPath = '/api/site/research/legacy-analysis/requests/';

  async function read(path, params, signal) {
    const url = path + (params ? '?' + new URLSearchParams(params) : '');
    const response = path.startsWith('/api/site/research/')
      ? await readResearch(url, signal) : await fetch(url, {signal});
    if (response.status === 401) throw Error('登录已过期，请重新登录。');
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw Error(payload.errors?.[0]?.message || payload.error || '读取失败，请重试。');
    }
    const payload = await response.json();
    return payload.data ?? payload;
  }

  function readResearch(url, signal) {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const next = researchReads.then(async () => {
      for (let attempt = 0; ; attempt++) {
        signal?.throwIfAborted();
        const delay = 240 - (performance.now() - lastResearchStart);
        if (delay > 0) await sleep(delay);
        signal?.throwIfAborted();
        lastResearchStart = performance.now();
        const siteSession = legacyHistory.dataset.auth === 'site';
        const response = await fetch(siteSession ? url : url.replace('/api/site/research/', '/v1/google/'), {
          signal,
          ...(siteSession || !productKey ? {} : {headers: {Authorization: 'Bearer ' + productKey}}),
        });
        if (response.status !== 429 || attempt === 2) return response;
        await response.body?.cancel();
        await sleep(Math.min(3000, 1000 * (Number(response.headers.get('Retry-After')) || 1)));
      }
    });
    researchReads = next.catch(() => {});
    return next;
  }

  function hostname(value) {
    if (!/^(?:https?:\/\/)?[^\s]+\.[^\s]+$/i.test(value)) return '';
    try {
      const url = new URL(/^https?:\/\//i.test(value) ? value : 'https://' + value);
      if (url.username || url.password || url.port || !/^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z][a-z0-9-]*$/i.test(url.hostname)) return '';
      return url.hostname.replace(/^www\./i, '').toLowerCase();
    } catch {
      return '';
    }
  }

  function scopedRows(data, query) {
    return (data.requests || []).filter(row => !query || lower(row.query) === lower(query));
  }

  function newsEvidence(entry) {
    const item = entry.item;
    return `${display(item)}<p class="ga-meta">${esc(item.publisher || '媒体未知')} · ${item.published_at ? '发布时间 ' + when(item.published_at) : '采集时间 ' + when(entry.row.completed_at)}</p>
      ${item.snippet ? '<p>' + esc(item.snippet) + '</p>' : ''}
      <button type="button" class="secondary mini" data-ga-request="${esc(entry.row.request_id)}">查看原始请求</button>`;
  }

  function repeatedPhrases(entries, query) {
    const ignored = new Set(('a an and are as at be by for from has how in is it of on or that the their this to was were what when which will with you your ' + lower(query)).split(/[^\p{L}\p{N}]+/u));
    const phrases = new Map();
    const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, {granularity:'word'}) : null;
    for (const entry of entries) {
      const title = lower(entry.item.title);
      const tokens = segmenter ? Array.from(segmenter.segment(title)).filter(part => part.isWordLike)
        .map(part => part.segment) : (title.match(/[\p{L}\p{N}]+/gu) || []);
      const seen = new Set();
      for (let i = 1; i < tokens.length; i++) {
        const words = [tokens[i - 1], tokens[i]];
        if (words.some(word => ignored.has(word) || word.length < 2)) continue;
        const phrase = words.join(' ');
        if (seen.has(phrase)) continue;
        seen.add(phrase);
        if (!phrases.has(phrase)) phrases.set(phrase, []);
        phrases.get(phrase).push(entry);
      }
    }
    return [...phrases].filter(([, group]) => group.length >= 2)
      .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0])).slice(0, 5);
  }

  const demoTopics = [
    ['产业与商业化', /\b(?:commercial\w*|industr(?:y|ial)|manufactur\w*|production line|gigafactor\w*)\b/i],
    ['储能应用', /\b(?:energy storage|grid storage|stationary storage|grid.scale)\b/i],
    ['循环与寿命', /\b(?:cycle life|cycling stability|long life|lifespan|capacity retention)\b/i],
  ];

  function news(data, query) {
    const rows = scopedRows(data, query);
    if (!rows.length) return section('news', 'Google News · 新闻简报', '<p>当前范围没有找到该关键词的公开新闻快照。</p>');
    const entries = rows.flatMap(row => (row.items || []).map(item => ({item, row})));
    const distinct = new Map();
    for (const entry of entries) {
      const {title, snippet} = entry.item;
      if (title || snippet) {
        const key = (title || '') + (snippet && snippet !== title ? '\n' + snippet : '');
        if (!distinct.has(key)) distinct.set(key, entry);
      }
    }
    const unique = [...distinct.values()];
    const publishers = new Map();
    for (const entry of entries) {
      const key = entry.item.publisher;
      if (!key) continue;
      if (!publishers.has(key)) publishers.set(key, []);
      publishers.get(key).push(entry);
    }
    const top = [...publishers].sort((a, b) => b[1].length - a[1].length).slice(0, 5);
    const isDemo = lower(query) === demo;
    const topics = isDemo ? demoTopics.map(([name, pattern]) => [name, unique.filter(({item}) =>
      pattern.test((item.title || '') + ' ' + (item.snippet || '')))]).filter(([, group]) => group.length)
      : repeatedPhrases(unique, query);
    const successful = rows.filter(row => row.classification === 'results' || row.classification === 'empty').length;
    return section('news', 'Google News · 新闻简报',
      `<p class="ga-facts"><strong>${successful} / ${rows.length} 次成功采样</strong><span>${entries.length} 条保存记录</span><span>${unique.length} 条不同标题／摘要</span></p>
       <p class="ga-meta">${when(rows.at(-1)?.completed_at)} 至 ${when(rows[0]?.completed_at)} · 地区 ${esc([...new Set(rows.map(row => row.query_conditions?.country_code || '未知'))].join(' / '))}
       ${data.has_more ? ' · 当前只读最近一批最多 20 次搜索记录' : ''}</p>
       ${topics.length ? '<h4>' + (isDemo ? '报道主题 · 按词组匹配标题与摘要' : '标题中反复出现的词组') + '</h4>' +
         topics.map(([name, group]) => evidence(`${name} · ${group.length} / ${unique.length} 条`, list(group, newsEvidence))).join('') : ''}
       ${top.length ? '<h4>常见报道来源</h4>' + top.map(([name, group]) => evidence(`${name} · ${group.length} / ${entries.length} 条记录`, list(group, newsEvidence))).join('') : ''}
       ${unique.length ? evidence('查看不同标题与摘要', list(unique, newsEvidence)) : ''}
       ${evidence('查看采样请求', list(rows, row => `${when(row.completed_at)} · ${esc(grHistoryStatus(row.classification))}
         <button type="button" class="secondary mini" data-ga-request="${esc(row.request_id)}">查看原始请求</button>`))}`);
  }

  function web(data, criteria) {
    if (!data.samples) return section('web', 'Google Web · 网站可见度', '<p>当前关键词与条件下没有可用的公开网页快照。</p>');
    const scope = data.scope || {};
    const top = data.top10 || {};
    const pair = top.pair || [];
    const latest = data.latest_results || [];
    const observed = (rank) => rank == null ? '未观测' : '第 ' + rank + ' 名';
    const monitor = (target) => `<button type="button" class="secondary mini" data-ga-monitor="${esc(target)}">加入关键词监测</button>`;
    const snapshots = data.timeline || [];
    const requestedDomain = criteria.domain || data.auto_domains?.[0] || '';
    const coverage = top.samples ? `可完整核对前 10 名的样本 ${top.samples} 次`
      : '可完整核对前 10 名的样本不足，已观察到的排名仍可查看。';
    const score = top.samples ? list(top.domains, domain => {
      const trend = pair.length === 2 ? ` · 最近两次 ${observed(domain.previous_rank)} → ${observed(domain.latest_rank)}` : '';
      return `<strong>${esc(domain.domain)}</strong><p>进入前 10 名 ${domain.hits} / ${top.samples} 次${trend}</p>`;
    }, 'ga-ranks') : list(data.domains || [], domain =>
      `<strong>${esc(domain.domain)}</strong><p>最近一份快照 · ${observed(domain.latest_rank)}</p>`, 'ga-ranks');
    const comparison = pair.length === 2 ? `<h4>前 10 名结果变化</h4><p>${when(pair[1].observed_at)} → ${when(pair[0].observed_at)} · 新出现 ${top.new_urls.length} 个网址 · 未再次出现 ${top.missing_urls.length} 个网址</p>
      ${evidence('查看变化网址与两次原始请求', list(top.new_urls, url => '新出现 · ' + link(url)) +
        list(top.missing_urls, url => '未再次出现 · ' + link(url)) + list(pair, s =>
          `<button type="button" class="secondary mini" data-ga-request="${esc(s.request_id)}">查看 ${when(s.observed_at)} 的请求</button>`))}`
      : '<p class="ga-meta">对比变化需要两次搜索条件一致、且前 10 名完整的观测。</p>';
    return section('web', 'Google Web · 网站可见度',
      `<p class="ga-facts"><strong>${data.samples} 次有效观测</strong><span>${coverage}</span></p>
       <p class="ga-meta">最近 ${when(data.latest)} · 地区 / 语言 / 设备 ${scopeText(scope)} · Google 第 ${esc(scope.page ?? '未知')} 页
       ${data.truncated ? ' · 已达到样本上限' : ''}</p>
       ${score}${comparison}
       ${latest.length ? evidence('查看最近一份快照的网页', list(latest, item => `${observed(item.position)} · ${link(item.link, item.title)}`)
         + (snapshots.length ? `<button type="button" class="secondary mini" data-ga-request="${esc(snapshots.at(-1).request_id)}">查看原始请求</button>` : '')) : ''}
       ${monitor(requestedDomain)}
       ${data.scope_choices?.length > 1 ? `<label>其他已存搜索条件<select id="gaScope">
          ${data.scope_choices.map(s => `<option value="${esc(s.id)}" ${s.id === data.scope_id ? 'selected' : ''}>${scopeText(s)} · 第 ${esc(s.page)} 页 · ${s.samples} 条</option>`).join('')}</select></label>` : ''}`);
  }

  async function savedTrend(query, filters, signal) {
    const match = row => lower(row?.query) === lower(query) &&
      (!filters.country || lower(row?.region || row?.query_conditions?.gl) === filters.country) &&
      (!filters.language || lower(row?.language || row?.query_conditions?.hl) === filters.language);
    const getSeries = record => match(record) && new Date(record.completed_at) >= new Date(filters.from)
      ? record.topics?.map(item => item.numeric_trend_series).find(Boolean) : null;
    for (const {record} of quickRecords) {
      if (record.source !== 'google_trends') continue;
      const series = getSeries(record);
      if (series) return series;
    }
    const job = quickRun?.entries?.find(entry => entry.status === 'succeeded' && entry.id && entry.token &&
      entry.params?.source === 'google_trends' && match(entry.params));
    if (!job) return null;
    const response = await fetch('/api/quick-request/' + encodeURIComponent(job.id) + '/data', {
      signal, headers: {'X-SearchFlow-Quick': '1', 'X-Quick-Token': job.token},
    });
    return response.ok ? getSeries(await response.json()) : null;
  }

  function trends([data, keywordData, privateSeries], query) {
    const series = [...(keywordData.requests || []).flatMap(row => (row.items || []).map(item => item.numeric_trend_series)),
      privateSeries]
      .find(item => item && lower(item.query) === lower(query));
    const matches = (data.requests || []).flatMap(row => (row.items || [])
      .filter(item => item.kind === 'trending_now_topic' && lower(item.topic).includes(lower(query)))
      .map(item => ({item, row})));
    const points = series?.points?.filter(point => point.has_data !== false && point.hasData !== false);
    return section('trends', 'Google Trends · 搜索热度',
      (points?.length ? qTrendChart({numeric_trend_series: {...series, points}})
        : '<p>当前条件下没有可读取的关键词热度曲线。</p>') +
      (matches.length ? evidence('查看地区热榜记录', list(matches, ({item, row}) =>
        `${esc(item.topic)} · ${scopeText(row.query_conditions)} · ${when(row.completed_at)} · ${esc(item.approx_traffic_label || '搜索量标记未知')}
         <button type="button" class="secondary mini" data-ga-request="${esc(row.request_id)}">查看原始请求</button>`))
        : '<p class="ga-meta">最近读取的最多 15 份地区热榜快照没有匹配话题。</p>'));
  }

  function images(data, query) {
    const rows = scopedRows(data, query);
    const items = rows.flatMap(row => (row.items || []).map(item => ({item, row})));
    return section('images', 'Google Images · 图片来源', items.length
      ? `<p>${rows.length} 次搜索 · ${items.length} 条图片记录 · 最近采集 ${when(rows[0].completed_at)}</p>` +
        evidence('查看图片链接与来源网页', list(items, ({item, row}) =>
          `${display(item)} · ${item.width && item.height ? item.width + ' × ' + item.height : '尺寸未知'}
           <p>${item.source_page_url ? link(item.source_page_url, '来源网页') : '来源网页未知'}${item.thumbnail_url ? ' · ' + link(item.thumbnail_url, '打开缩略图') : ''}</p>
           <button type="button" class="secondary mini" data-ga-request="${esc(row.request_id)}">查看原始请求</button>`))
      : '<p>当前条件没有匹配的公开图片快照。</p>');
  }

  async function maps() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem('sf-maps-last') || 'null'); } catch {}
    const fallback = '<p>在「快速使用」获取或恢复 Maps 快照，可按城市和半径查看商家。</p><a href="#maps">打开 Google Maps</a>';
    if (!saved?.id || !saved?.token) return section('maps', 'Google Maps · 已存商家', fallback);
    const signal = controller.signal;
    const res = await fetch('/api/maps/jobs/' + encodeURIComponent(saved.id) + '/results', {
      signal, headers: {'X-SearchFlow-Quick': '1', 'X-Quick-Token': saved.token},
    });
    if (!res.ok) return section('maps', 'Google Maps · 已存商家', fallback);
    const snapshot = await res.json();
    const p = snapshot.parameters || {};
    const places = snapshot.result_cache?.places;
    if (!Array.isArray(places) || !places.length) return section('maps', 'Google Maps · 已存商家', fallback);
    return section('maps', 'Google Maps · 最近保存的商家快照',
      `<p>${esc(p.query || p.included_type || '商家搜索')} · ${esc(p.city || '城市未知')} · 半径 ${esc(p.radius_m ?? '未知')} 米 · ${places.length} 家</p>` +
      evidence('对照样本内的商家评分、评价数和距离', list(places, place =>
        `${link(place.google_maps_uri, place.name || '名称未知')}<p>${esc(place.formatted_address || '地址未知')} · 评分 ${esc(place.rating ?? '未知')} · 评价 ${esc(place.user_rating_count ?? '未知')} · 距中心 ${esc(place.distance_m ?? '未知')} 米</p>`))
      + '<a href="#maps">打开 Maps 明细与下载</a>');
  }

  function baseCriteria() {
    return {days: $('gaDays').value, region: region.value, language: $('gaLanguage').value,
      domain: $('gaDomain').value.trim(), competitors: $('gaCompetitors').value.trim()};
  }

  async function analyze(query, criteria = baseCriteria(), {scope = '', pick = false, asKeyword = false} = {}) {
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    status.textContent = '正在读取已保存的 Google 数据…';
    result.replaceChildren();
    applied = null;
    const timeout = setTimeout(() => {
      if (controller.signal === signal) controller.abort('timeout');
    }, 30000);
    try {
      const domain = asKeyword ? '' : hostname(query);
      const cutoff = new Date();
      cutoff.setUTCHours(0, 0, 0, 0);
      cutoff.setUTCDate(cutoff.getUTCDate() - Number(criteria.days) + 1);
      const filters = {from: cutoff.toISOString(), limit: '20',
        ...(criteria.region && {country: criteria.region.toLowerCase()}),
        ...(criteria.language && {language: criteria.language})};
      if (domain) {
        const data = await read(observationsPath, {...filters, source:'google_web', domain}, signal);
        const rows = data.requests || [];
        const terms = [...new Set(rows.map(row => row.query).filter(Boolean))];
        const suggestions = terms.length ? '<p>这些已存搜索中看到了该域名。选择关键词，查看它与竞品的可见度。</p>'
          + list(terms, term => `<button type="button" class="secondary mini" data-ga-keyword="${esc(term)}" data-ga-domain="${esc(domain)}">${esc(term)}</button>`)
          : '<p>当前范围没有查到该域名的公开网页快照。可以输入一个关键词，并把此域名设为对比目标。</p>';
        result.innerHTML = section('domain', `查看 ${esc(domain)} 的已存搜索`, suggestions);
        status.textContent = `已读取 ${rows.length} 次匹配请求 · 最近 ${criteria.days} 天（UTC）`;
        $('gaDomain').value = domain;
        return;
      }
      const webParams = {...criteria, query, scope};
      const initialWeb = read('/api/product/analytics', webParams, signal).then(data => {
        if (scope || data.scope?.page === 1) return data;
        const firstPage = data.scope_choices?.find(entry => entry.page === 1);
        return firstPage ? read('/api/product/analytics', {...webParams, query:data.query, scope:firstPage.id}, signal) : data;
      });
      if (pick) {
        const saved = await initialWeb;
        query = saved.query;
        if (!query) throw Error('当前范围没有可试用的网页关键词。');
        input.value = query;
      }
      applied = {query, criteria, scope};
      const exact = {...filters, query, query_match:'exact'};
      const jobs = [
        ['web', 'Google Web', initialWeb, data => {
          applied.web = data;
          return web(data, criteria);
        }],
        ['news', 'Google News', read(observationsPath, {...exact, source:'google_news'}, signal), data => news(data, query)],
        ['trends', 'Google Trends', Promise.all([
          read(observationsPath, {...exact, source:'google_trends', query:'trending-now', limit:'15'}, signal),
          read(observationsPath, {...exact, source:'google_trends', limit:'5'}, signal),
          savedTrend(query, filters, signal).catch(() => null),
        ]), data => trends(data, query)],
        ['images', 'Google Images', read(observationsPath, {...exact, source:'google_images', limit:'5'}, signal), data => images(data, query)],
        ['maps', 'Google Maps', maps(), value => value],
      ];
      result.innerHTML = jobs.map(([id, title]) => `<div id="gaSource-${id}"><p class="ga-meta">正在读取 ${title}…</p></div>`).join('')
        + '<div id="gaRequestEvidence" tabindex="-1"></div>';
      let errors = 0;
      await Promise.all(jobs.map(async ([id, title, job, render]) => {
        try {
          const data = await job;
          if (!signal.aborted) $('gaSource-' + id).innerHTML = render(data);
        } catch (error) {
          if (controller.signal !== signal || (signal.aborted && signal.reason !== 'timeout')) return;
          errors++;
          $('gaSource-' + id).innerHTML = section(id, title,
            `<p>${esc(signal.aborted ? '读取超时，请缩小范围后重试。' : error.message)}</p>`);
        }
      }));
      if (controller.signal === signal && (!signal.aborted || signal.reason === 'timeout'))
        status.textContent = `最近 ${criteria.days} 天（UTC）· 已保存的搜索样本${errors ? ' · ' + errors + ' 个来源读取失败' : ''}`;
    } catch (error) {
      if (controller.signal === signal && (!signal.aborted || signal.reason === 'timeout'))
        status.textContent = signal.aborted ? '读取超时，请重试。' : error.message;
    } finally {
      clearTimeout(timeout);
    }
  }

  $('gaForm').addEventListener('submit', event => {
    event.preventDefault();
    if (input.value.trim()) analyze(input.value.trim());
  });
  $('gaNewsExample').onclick = () => {
    input.value = demo;
    $('gaDays').value = '90';
    $('gaDomain').value = '';
    $('gaCompetitors').value = '';
    region.value = '';
    $('gaLanguage').value = '';
    analyze(demo);
  };
  $('gaWebExample').onclick = () => analyze('', baseCriteria(), {pick:true});
  result.addEventListener('change', event => {
    if (event.target.id === 'gaScope' && applied)
      analyze(applied.query, applied.criteria, {scope:event.target.value, asKeyword:true});
  });
  result.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.gaKeyword) {
      input.value = button.dataset.gaKeyword;
      $('gaDomain').value = button.dataset.gaDomain;
      analyze(input.value, baseCriteria(), {asKeyword:true});
    } else if (button.hasAttribute('data-ga-monitor') && applied) {
      const scope = applied.web?.scope || {};
      $('monitorQuery').value = applied.query;
      $('monitorDomain').value = button.dataset.gaMonitor;
      $('monitorCompetitors').value = applied.criteria.competitors.replace(/，/g, ',');
      for (const [id, value] of [['monitorRegion', scope.region || applied.criteria.region], ['monitorLanguage', scope.language || applied.criteria.language]]) {
        const select = $(id);
        const option = [...select.options].find(o => lower(o.value) === lower(value));
        select.selectedIndex = option ? option.index : 0;
      }
      window.addEventListener('hashchange', () => {
        $('monitorForm').scrollIntoView({block:'start'});
        $(button.dataset.gaMonitor ? 'monitorQuery' : 'monitorDomain').focus({preventScroll:true});
      }, {once:true});
      location.hash = 'monitors';
    } else if (button.dataset.gaRequest) {
      const id = button.dataset.gaRequest;
      const target = $('gaRequestEvidence');
      button.closest('.ga-section').after(target);
      button.disabled = true;
      try {
        const {request} = await read(requestPath + encodeURIComponent(id), null, controller.signal);
        if (!target.isConnected) return;
        const original = (request.items || []).map(item => item.kind === 'image'
          ? '<p>' + display(item) + ' · ' + link(item.source_page_url, '来源网页') + '</p>'
          : item.kind === 'keyword_interest_over_time' ? qTrendChart(item) : grHistoryItem(item));
        target.innerHTML = section('request', '原始请求证据',
          `<p>${esc(request.query || '关键词未知')} · ${when(request.completed_at)} · ${scopeText(request.query_conditions)} · ${esc(id)}</p>${original.join('')}`);
        target.focus();
      } catch (error) {
        target.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    }
  });

  advanced.addEventListener('toggle', () => {
    if (advanced.open && !analysisLoaded) loadAnalysis().catch(error => notice(error.message, true));
  });
  history.addEventListener('toggle', () => {
    if (!history.open) return;
    legacyHistory.hidden = false;
    loadGoogleResearch().catch(error => notice(error.message, true));
  });
  const originalLoad = loadProductView;
  loadProductView = async function () {
    if (view !== 'analysis' || advanced.open) return originalLoad();
  };
  const originalShow = showView;
  showView = function () {
    if (location.hash === '#research') window.history.replaceState(null, '', '#analysis');
    originalShow();
    if (view === 'analysis') {
      $('pageTitle').textContent = 'Google 分析';
      $('pageSubtitle').textContent = '查找发现、核对原始证据、持续监测';
      legacyHistory.hidden = false;
    } else if (view === 'insights') {
      $('pageTitle').textContent = '文本分析';
    }
  };
})();
