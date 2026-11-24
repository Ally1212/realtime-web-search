(() => {
  'use strict';
  const examples = {keyword:'AI coding assistants',website:'augmentcode.com'};
  const input = $('gaInput');
  const result = $('gaResult');
  const status = $('gaStatus');
  const region = $('gaRegion');
  const legacyHistory = $('researchView');
  const history = $('gaHistory');
  const advanced = $('gaReports');
  let controller;
  let applied = null;
  const startedModes = new Set();
  let historyRequested = false;
  region.replaceChildren(new Option('全部', ''), ...Array.from($('analysisRegion').options)
    .filter(option => option.value).map(option => new Option(option.text, option.value)));
  history.append(legacyHistory);
  legacyHistory.querySelectorAll('details').forEach(detail => { detail.open = true; });
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
  const evidence = (label, html) => `<details open><summary>${esc(label)}</summary>${html}</details>`;
  const records = (label, html) => `<div class="ga-records" role="region" aria-label="${esc(label)}" tabindex="0">${html}</div>`;
  const list = (rows, render, css = 'ga-evidence') => `<ul class="${css}">${rows.map(row => '<li>' + render(row) + '</li>').join('')}</ul>`;
  const icon = name => grIcon(({web:'search',news:'dataset',trends:'trend',images:'dataset',maps:'location',request:'dataset'})[name]||'analysis') || grIcon('analysis');
  const section = (id, title, body) => `<section class="ga-section" aria-labelledby="gaTitle-${id}"><h3 class="ga-heading-icon" id="gaTitle-${id}">${icon(id)}${title}</h3>${body}</section>`;
  const display = (item, url = item.publisher_url || item.url) => link(url, item.title || item.topic || url);
  const observationsPath = '/api/site/research/legacy-observations';
  const requestPath = '/api/site/research/legacy-analysis/requests/';
  const chartColors = ['#276657', '#355c9a', '#9a4e36', '#705891'];

  function publish(source, {finding, chart = '', charts = [], data, metrics = []}) {
    applied.outputs.set(source, {finding, data, metrics});
    const group = $('gaChart-' + source);
    if (!group) return;
    group.innerHTML = [chart,...charts].filter(Boolean).map(content => `<article class="ga-chart">${content}</article>`).join('');
    if (source === 'trends' && chart) {
      $('gaLeadChart').innerHTML = `<article class="ga-chart">${chart}</article>`;
      $('gaLeadChart').hidden = false;
    }
  }

  function reportChart(title, caption, content, source = 'analysis') {
    const labels={web:'Google 网页',news:'Google 新闻',trends:'Google 趋势',images:'Google 图片',maps:'Google 地图'};
    return `<header class="ga-card-header"><h3 class="ga-heading-icon">${icon(source)}${esc(title)}</h3>
      ${labels[source]?`<span class="ga-card-source">${labels[source]}</span>`:''}</header><p class="ga-meta">${esc(caption)}</p>${content}`;
  }

  function bars(rows, denominator) {
    if (!rows.length || denominator <= 0) return '';
    return `<ul class="ga-bars">${rows.map(({label, count, target}) =>
      `<li><div class="ga-bar-label">${target
        ? `<button type="button" class="secondary mini" data-ga-jump="${esc(target)}">${esc(label)}</button>`
        : `<span>${esc(label)}</span>`}<strong class="ga-bar-number">${count} / ${denominator}</strong></div>
        <span class="ga-bar-track" aria-hidden="true"><span class="ga-bar-fill" style="width:${Math.min(100, count / denominator * 100)}%"></span></span></li>`).join('')}</ul>`;
  }

  function dataTable(title, headers, rows) {
    return `<div class="ga-table" role="region" aria-label="${esc(title)}" tabindex="0">
      <table><caption>${esc(title)}</caption><thead><tr>${headers.map(label => '<th scope="col">' + esc(label) + '</th>').join('')}</tr></thead>
      <tbody>${rows.map(([label,...values]) => '<tr><th scope="row">' + esc(label) + '</th>' +
        values.map(value => '<td>' + esc(value ?? '未观测') + '</td>').join('') + '</tr>').join('')}</tbody></table></div>`;
  }

  function lineChart(caption, stamps, series, lowerBound, upperBound, {reversed = false, connected = true} = {}) {
    const times = stamps.map(stamp => Date.parse(stamp));
    const range = Math.max(1, times.at(-1) - times[0]);
    const x = i => 51 + (times[i] - times[0]) / range * 490;
    const y = value => 15 + (reversed ? value - lowerBound : upperBound - value) / Math.max(1, upperBound-lowerBound) * 108;
    const lines = series.map(({values}, index) => values.map((value, i) => {
      if (!Number.isFinite(value)) return '';
      const previous = values[i-1];
      const color = chartColors[index];
      return (connected&&Number.isFinite(previous) ? `<line x1="${x(i-1)}" y1="${y(previous)}" x2="${x(i)}" y2="${y(value)}" stroke="${color}" stroke-width="2.4"/>` : '') +
        `<circle cx="${x(i)}" cy="${y(value)}" r="2.2" fill="${color}"/>`;
    }).join('')).join('');
    const grid = (reversed ? [...new Set([lowerBound,2,3,5,upperBound])].filter(value=>value>=lowerBound&&value<=upperBound) : [lowerBound,upperBound]).map(value => `<line x1="51" y1="${y(value)}" x2="541" y2="${y(value)}" stroke="#dde3e8"/><text x="44" y="${y(value)+4}" text-anchor="end" font-size="12">${value}</text>`).join('');
    const sameDay = stamps.length && stamps.every(stamp=>stamp.slice(0,10)===stamps[0].slice(0,10));
    const tickIndexes = stamps.length <= 1 ? [0] : [0,stamps.length-1];
    if (stamps.length>2) {
      const middle = Math.floor(stamps.length/2);
      if (x(middle)-x(0)>120 && x(stamps.length-1)-x(middle)>120) tickIndexes.splice(1,0,middle);
    }
    const tickLabel = stamp => {
      const utc = new Date(stamp).toISOString();
      if (sameDay) return utc.slice(11,19);
      const sharedDay = tickIndexes.filter(i=>stamps[i]?.slice(0,10)===stamp.slice(0,10)).length>1;
      return utc.slice(5,10) + (sharedDay?' '+utc.slice(11,16):'');
    };
    const dateLabels = tickIndexes.filter(i=>stamps[i]).map(i=>{
      const anchor = i===0?'start':i===stamps.length-1?'end':'middle';
      return `<text x="${x(i)}" y="146" text-anchor="${anchor}" font-size="15">${esc(tickLabel(stamps[i]))}</text>`;
    }).join('');
    const labels = series.map(({label}, index) => `<strong style="color:${chartColors[index]}">${esc(label)}</strong>`).join(' · ');
    return `<figure><figcaption>${esc(caption)} · ${sameDay ? esc(stamps[0].slice(0,10))+' UTC · ' : 'UTC · '}${labels}</figcaption><svg viewBox="0 0 560 155" aria-hidden="true">${grid}${dateLabels}${lines}</svg></figure>` +
      dataTable('采样明细（UTC）', ['观测时间',...series.map(item=>item.label)],
        stamps.map((stamp,i)=>[(sameDay ? new Date(stamp).toISOString().slice(11,19) : dateTime.format(new Date(stamp))),...series.map(item=>item.values[i])]));
  }

  async function read(path, params, signal) {
    const url = path + (params ? '?' + new URLSearchParams(params) : '');
    const response = path.startsWith('/api/site/research/')
      ? await readResearch(url, signal) : await fetch(url, {signal});
    if (response.status === 401) throw Error('登录已过期，请重新登录。');
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw Error(googleResearchError(payload,response.status) || payload.errors?.[0]?.message || payload.error || '读取失败，请重试。');
    }
    const payload = await response.json();
    return payload.data ?? payload;
  }

  function readResearch(url, signal) {
    const siteSession = legacyHistory.dataset.auth === 'site';
    return googleResearchRead(siteSession ? url : url.replace('/api/site/research/', '/v1/google/'), {
      signal,
      ...(siteSession || !productKey ? {} : {headers:{Authorization:'Bearer ' + productKey}}),
    });
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

  function repeatedPhrases(entries, query, minimum = 2, limit = 5) {
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
    return [...phrases].filter(([, group]) => group.length >= minimum)
      .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0])).slice(0, limit);
  }

  function distinctNews(entries) {
    const distinct = new Map();
    for (const entry of entries) {
      const {title, snippet} = entry.item;
      if (title || snippet) {
        const key = (title || '') + (snippet && snippet !== title ? '\n' + snippet : '');
        if (!distinct.has(key)) distinct.set(key, entry);
      }
    }
    return [...distinct.values()];
  }

  function newsComparison(rows, query) {
    const key = row => {
      const {country_code, language_code} = row.query_conditions || {};
      const defined = value => value && lower(value) !== 'unknown';
      return defined(country_code) && defined(language_code) ? lower(country_code) + '/' + lower(language_code) : '';
    };
    const eligible = rows.filter(row => row.classification === 'results' && key(row) &&
      !['hit','cache_hit','cached'].includes(lower(row.cache_status)));
    eligible.sort((a,b) => Date.parse(b.completed_at) - Date.parse(a.completed_at));
    const current = eligible[0];
    const prior = current && eligible.find(row => key(row) === key(current) &&
      Date.parse(current.completed_at) - Date.parse(row.completed_at) >= 86400000);
    if (!prior) return null;
    const texts = row => distinctNews((row.items || []).map(item => ({item,row})));
    const earlier = texts(prior), later = texts(current);
    if (earlier.length < 5 || later.length < 5) return null;
    const topics = entries => repeatedPhrases(entries,query,1,Infinity);
    const older = new Map(topics(earlier));
    const next = topics(later).map(([name,group]) => ({name, group, before:older.get(name) || []}))
      .filter(({group,before}) => group.length >= 2 && group.length/later.length - before.length/earlier.length >= .15)
      .sort((a,b) => (b.group.length-a.group.length)/later.length - (b.before.length-a.before.length)/earlier.length)[0];
    return next ? {...next, current, prior, oldTotal:earlier.length, newTotal:later.length, market:key(current)} : null;
  }

  function news(data, query) {
    const rows = scopedRows(data, query);
    const insufficient = {kind:'insufficient', title:'报道样本不足', detail:'这个范围内还没有可用的新闻主题。', target:'gaSource-news'};
    if (!rows.length) {
      publish('news', {finding:insufficient, data:[]});
      return section('news', 'Google News · 新闻简报', '<p>当前范围没有找到该关键词的公开新闻快照。</p>');
    }
    const entries = rows.flatMap(row => (row.items || []).map(item => ({item, row})));
    const unique = distinctNews(entries);
    const publishers = new Map();
    for (const entry of entries) {
      const key = entry.item.publisher;
      if (!key) continue;
      if (!publishers.has(key)) publishers.set(key, []);
      publishers.get(key).push(entry);
    }
    const top = [...publishers].sort((a, b) => b[1].length - a[1].length).slice(0, 5);
    const topics = repeatedPhrases(unique, query);
    topics.sort((a,b) => b[1].length-a[1].length);
    const notable = newsComparison(rows, query);
    const themes = topics.map(([label,group], i)=>({label,count:group.length,target:'gaTopic-'+i}));
    const finding = notable ? {
      kind:'detected', title:notable.before.length ? `「${notable.name}」在最新新闻样本中更常见` : `最新新闻样本出现「${notable.name}」`,
      detail:`${notable.market} · 提及 ${notable.before.length}/${notable.oldTotal} → ${notable.group.length}/${notable.newTotal} 条 · 两份采样相隔至少一天`,
      action:'查看新增报道，判断是否值得继续追踪。', target:'gaNewsChange',
    } : topics.length ? {
      kind:'detected', title:`报道提到「${topics[0][0]}」`,
      detail:`${topics[0][1].length} / ${unique.length} 条去重文本 · 来自 ${rows.length} 次新闻采样`,
      action:'打开相关标题和摘要，找出值得调研的具体问题。', target:'gaTopic-0',
    } : insufficient;
    const publishersChart = top.map(([label,group],i)=>({label,count:group.length,target:'gaPublisher-'+i}));
    const newsDays = new Map();
    for (const row of rows) {
      if (!Number.isFinite(Date.parse(row.completed_at))) continue;
      const day = new Date(row.completed_at).toISOString().slice(0,10);
      newsDays.set(day,(newsDays.get(day)||0)+1);
    }
    const dayCounts = [...newsDays].sort(([a],[b])=>a.localeCompare(b)).slice(-12).map(([label,count])=>({label,count}));
    publish('news', {finding, data:{rows, topics:themes, comparison:notable},
      chart:themes.length ? reportChart('新闻主题', '提及数 / 去重标题与摘要总数。', bars(themes,unique.length),'news') : '',
      charts:[
        top.length ? reportChart('报道来源', '按保存记录计数，同一报道可能出现在多次采样中。',bars(publishersChart,entries.length),'news') : '',
        dayCounts.length ? reportChart('新闻采样日期', '每个 UTC 日期保存的采样次数。',bars(dayCounts,rows.length),'news') : '',
      ],
      metrics:[...themes.map(theme=>[theme.label,theme.count,unique.length,'提及 / 去重文本']),
        ...top.map(([name,group])=>[name,group.length,entries.length,'记录 / 保存记录']),
        ...dayCounts.map(({label,count})=>[label,count,rows.length,'新闻采样 / 本次读取的采样数'])],
    });
    const successful = rows.filter(row => row.classification === 'results' || row.classification === 'empty').length;
    return section('news', 'Google News · 新闻简报',
      `<p class="ga-facts"><strong>${successful} / ${rows.length} 次成功采样</strong><span>${entries.length} 条保存记录</span><span>${unique.length} 条不同标题／摘要</span></p>
       <p class="ga-meta">${when(rows.at(-1)?.completed_at)} 至 ${when(rows[0]?.completed_at)} · 地区 ${esc([...new Set(rows.map(row => row.query_conditions?.country_code || '未知'))].join(' / '))}
       ${data.has_more ? ' · 当前读取最近一批最多 30 次搜索记录' : ''}</p>
       ${topics.length ? '<h4>' + '标题中反复出现的词组' + '</h4>' +
         topics.map(([name, group], i) => `<details open id="gaTopic-${i}"><summary>${esc(name)} · ${group.length} / ${unique.length} 条</summary>${records(name,list(group, newsEvidence))}</details>`).join('') : ''}
       ${notable ? `<details open id="gaNewsChange"><summary>两次新闻样本对比</summary><p>${when(notable.prior.completed_at)} → ${when(notable.current.completed_at)} · ${esc(notable.market)}</p>
          <p>较早样本 ${notable.before.length} / ${notable.oldTotal}，最新样本 ${notable.group.length} / ${notable.newTotal}。</p>
          <h4>最新样本中匹配的报道</h4>${list(notable.group,newsEvidence)}
          ${notable.before.length?'<h4>较早样本中匹配的报道</h4>'+list(notable.before,newsEvidence):''}
          ${[notable.prior,notable.current].map(row=>`<button type="button" class="secondary mini" data-ga-request="${esc(row.request_id)}">查看 ${when(row.completed_at)} 的原始采样</button>`).join(' ')}</details>` : ''}
       ${top.length ? '<h4>常见报道来源</h4>' + top.map(([name, group],i) => `<details open id="gaPublisher-${i}"><summary>${esc(name)} · ${group.length} / ${entries.length} 条记录</summary>${records(name,list(group,newsEvidence))}</details>`).join('') : ''}
       ${unique.length ? evidence('不同标题与摘要',records('新闻标题与摘要',list(unique, newsEvidence))) : ''}
       ${evidence('采样请求', records('新闻采样请求',list(rows, row => `${when(row.completed_at)} · ${esc(grHistoryStatus(row.classification))}
         <button type="button" class="secondary mini" data-ga-request="${esc(row.request_id)}">查看原始请求</button>`)))}`);
  }

  function web(data, criteria) {
    if (!data.samples) {
      publish('web',{finding:{kind:'insufficient',title:'网页排名样本不足',detail:'当前范围没有查到这个词的公开网页快照。',target:'gaSource-web'},data});
      return section('web', 'Google Web · 网站可见度', '<p>当前关键词与条件下没有可用的公开网页快照。</p>');
    }
    const scope = data.scope || {};
    const top = data.top10 || {};
    const pair = top.pair || [];
    const latest = data.latest_results || [];
    const observed = (rank) => rank == null ? '未观测' : '第 ' + rank + ' 名';
    const monitor = (target) => `<button type="button" class="secondary mini" data-ga-monitor="${esc(target)}">加入关键词监测</button>`;
    const snapshots = data.timeline || [];
    const requestedDomain = criteria.domain || data.auto_domains?.[0] || '';
    const tracked = (data.domains || []).slice(0,4);
    const ranks = snapshots.filter(row=>Number.isFinite(Date.parse(row.fetched_at))).slice(-24);
    const rankSeries = tracked.map(({domain})=>({label:domain, values:ranks.map(row=>row.ranks[domain] ?? null)}));
    const rankValues = rankSeries.flatMap(row=>row.values).filter(Number.isFinite);
    const domainBars = top.samples ? (top.domains||[]).map(d=>({label:d.domain,count:d.hits}))
      : (data.domain_presence||[]).slice(0,6).map(d=>({label:d.domain,count:d.samples}));
    const movers = (top.domains||[]).filter(d=>d.previous_rank!=null&&d.latest_rank!=null&&d.previous_rank!==d.latest_rank)
      .sort((a,b)=>Math.abs(b.previous_rank-b.latest_rank)-Math.abs(a.previous_rank-a.latest_rank));
    const changed = pair.length===2 && (movers.length || top.new_urls.length || top.missing_urls.length);
    const finding = changed ? {kind:'detected', title:movers.length ?
      `${movers[0].domain} 从第 ${movers[0].previous_rank} 名变为第 ${movers[0].latest_rank} 名`
      : `前 10 名有 ${top.new_urls.length} 个网址新出现，${top.missing_urls.length} 个未再次出现`,
      detail:`${when(pair[1].observed_at)} → ${when(pair[0].observed_at)} · 同搜索条件`,
      action:'查看变化页面，再决定跟踪哪个网站。',target:'gaWebChanges'}
      : {kind:pair.length===2?'detected':'insufficient',title:pair.length===2?'前 10 名未观察到变化':'还不能判断网页排名变化',
        detail:`已有 ${data.samples} 份有效快照，其中 ${top.samples || 0} 份可完整核对前 10 名。`,
        action:'可以查看已观察到的排名，或把关键词加入监测。',target:'gaSource-web'};
    const chart = ranks.length && rankValues.length
      ? lineChart(top.complete_scope?'按日期查看实际名次':'按日期查看实际名次 · 搜索条件未完整记录',
        ranks.map(row=>row.fetched_at),rankSeries,1,Math.max(5,...rankValues),{reversed:true,connected:top.complete_scope===true}) : '';
    const distribution = (data.distribution||[]).map(({bucket,count})=>({label:bucket==='unknown'?'名次未知':bucket,count}));
    const rankedTotal = distribution.reduce((sum,row)=>sum+row.count,0);
    const countryCounts = new Map();
    for (const group of data.scope_choices||[]) {
      if (group.page!==1) continue;
      const country = group.region||'地区未知';
      countryCounts.set(country,(countryCounts.get(country)||0)+group.samples);
    }
    const countryBars = [...countryCounts].map(([label,count])=>({label,count})).sort((a,b)=>b.count-a.count);
    const firstPageTotal = countryBars.reduce((sum,row)=>sum+row.count,0);
    publish('web',{finding,data,chart:domainBars.length ? reportChart('网站出现次数',
      top.samples ? '分母为前 10 名完整的快照，图表按所选搜索条件生成。' : '分母为当前条件下的有效快照，显示域名出现次数。',
      bars(domainBars,top.samples||data.samples),'web') : '',
      charts:[
        chart ? reportChart('网页名次记录', '名次越小越靠前。未观测的点留空。',chart,'web') : '',
        rankedTotal ? reportChart('网页名次分布', '当前搜索条件下的网页结果，按观测名次分组。',bars(distribution,rankedTotal),'web') : '',
        firstPageTotal ? reportChart('网页采样地区', '该关键词的首页采样按地区统计，包含所有可选条件组。',bars(countryBars.slice(0,8),firstPageTotal),'web') : '',
      ],
      metrics:[...domainBars.map(row=>[row.label,row.count,top.samples||data.samples,top.samples?'进入前 10 名 / 完整快照':'出现 / 有效快照']),
        ...distribution.map(row=>[row.label,row.count,rankedTotal,'名次区间 / 网页记录总数']),
        ...countryBars.map(row=>[row.label,row.count,firstPageTotal,'地区首页采样 / 首页采样总数']),
        ...rankSeries.flatMap(({label,values})=>values.flatMap((value,i)=>value==null ? []
          : [[label+' · '+ranks[i].fetched_at,value,null,'已观测自然排名']]))]});
    const coverage = top.samples ? `可完整核对前 10 名的样本 ${top.samples} 次`
      : '可完整核对前 10 名的样本不足，已观察到的排名仍可查看。';
    const score = top.samples ? list(top.domains, domain => {
      const trend = pair.length === 2 ? ` · 最近两次 ${observed(domain.previous_rank)} → ${observed(domain.latest_rank)}` : '';
      return `<strong>${esc(domain.domain)}</strong><p>进入前 10 名 ${domain.hits} / ${top.samples} 次${trend}</p>`;
    }, 'ga-ranks') : list(data.domains || [], domain =>
      `<strong>${esc(domain.domain)}</strong><p>最近一份快照 · ${observed(domain.latest_rank)}</p>`, 'ga-ranks');
    const comparison = pair.length === 2 ? `<div id="gaWebChanges" tabindex="-1"><h4>前 10 名结果变化</h4><p>${when(pair[1].observed_at)} → ${when(pair[0].observed_at)} · 新出现 ${top.new_urls.length} 个网址 · 未再次出现 ${top.missing_urls.length} 个网址</p>
      ${evidence('查看变化网址与两次原始请求', list(top.new_urls, url => '新出现 · ' + link(url)) +
        list(top.missing_urls, url => '未再次出现 · ' + link(url)) + list(pair, s =>
          `<button type="button" class="secondary mini" data-ga-request="${esc(s.request_id)}">查看 ${when(s.observed_at)} 的请求</button>`))}</div>`
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

  function trendChange(points) {
    const window = points.filter(point=>!point.is_partial).slice(-6);
    if (window.length<6 || window.some(point=>point.value==null)) return null;
    const gaps = window.slice(1).map((point, i)=>Date.parse(point.timestamp)-Date.parse(window[i].timestamp));
    const minGap = Math.min(...gaps);
    if (minGap<=0 || Math.max(...gaps)>minGap*1.25) return null;
    const mean = values => values.reduce((sum, point)=>sum+point.value,0)/values.length;
    const previous = mean(window.slice(0,3)), recent = mean(window.slice(3));
    return {previous, recent, delta:recent-previous, from:window[0].timestamp, to:window.at(-1).timestamp};
  }

  function trends([data, keywordData, privateSeries], query, from) {
    const publicSeries = (keywordData.requests || []).flatMap(row => (row.items || [])
      .map(item => ({row,series:item.numeric_trend_series})))
      .find(({series}) => series && lower(series.query)===lower(query));
    const series = publicSeries?.series || (lower(privateSeries?.query)===lower(query)? privateSeries : null);
    const matches = (data.requests || []).flatMap(row => (row.items || [])
      .filter(item => item.kind === 'trending_now_topic' && lower(item.topic).includes(lower(query)))
      .map(item => ({item, row})));
    const points = (series?.points || []).filter(point=>Date.parse(point.timestamp)>=Date.parse(from))
      .map(({timestamp,value,is_partial,has_data,hasData})=>({timestamp, is_partial, value:
        Number.isFinite(value)&&value>=0&&value<=100&&has_data!==false&&hasData!==false ? value : null}))
      .sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
    const complete = points.filter(point=>!point.is_partial&&point.value!=null);
    const change = trendChange(points);
    const markets = new Set(matches.map(({row})=>row.query_conditions?.country_code).filter(Boolean));
    const format = new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1});
    const changed = change && Math.abs(change.delta)>=10;
    const rise = changed && change.delta>0;
    const regionLabel = series?.region || '地区未提供';
    const finding = changed ? {
      kind:'detected',title:rise?'这份样本的搜索热度上升':'这份样本的搜索热度下降',
      detail:`${regionLabel} · 相同频率的 3 点均值 ${format.format(change.previous)} → ${format.format(change.recent)} / 100 · ${when(change.from)} 至 ${when(change.to)}`,
      action:rise?'可结合相关报道，进一步调查升温集中在哪些具体问题。':'可继续观察，确认下降是否持续。',
      target:'gaSource-trends',
    } : matches.length ? {kind:'detected',title:'话题在地区热榜出现过',
      detail:`${matches.length} 条热榜记录 · ${markets.size ? '已知地区 '+[...markets].join(' / ') : '地区未提供'}`,
      action:'打开上榜记录和关联报道，查看具体事件。',target:'gaTrendHotlist'}
      : {kind:change?'detected':'insufficient',title:change?'近期相对热度没有明显变化':'热度趋势样本不足',
        detail:change?'相邻两个观测段的均值变化小于 10 个指数点。':'需要同一地区的连续有效热度点，才能判断方向。',
        target:'gaSource-trends'};
    const plot = points.slice(-120);
    const chart = complete.length ? lineChart('Google Trends 相对热度 0–100 · 未观测与未完成点留空',plot.map(p=>p.timestamp),
      [{label:regionLabel,values:plot.map(p=>p.is_partial ? null : p.value)}],0,100) : '';
    const marketCounts = new Map();
    for (const {row} of matches) {
      const market = row.query_conditions?.country_code || '地区未知';
      marketCounts.set(market,(marketCounts.get(market)||0)+1);
    }
    const marketBars = [...marketCounts].sort(([,a],[,b])=>b-a).map(([label,count])=>({label,count,target:'gaTrendHotlist'}));
    publish('trends',{finding,chart:chart ? reportChart('搜索热度',`${regionLabel} · ${complete.length} 个有效观测点 · 0–100 是相对指数`,chart,'trends') : '',
      charts:[marketBars.length?reportChart('地区热榜记录','按匹配话题的已存热榜记录计数。',bars(marketBars,matches.length),'trends'):''],
      data:{series:series ? {region:series.region,query:series.query,time_range:series.time_range,
        request_id:publicSeries?.row.request_id,points} : null, matches, change},
      metrics:[...points.filter(p=>p.value!=null&&!p.is_partial).map(p=>[p.timestamp,p.value,100,'相对热度 / 指数上限']),
        ...marketBars.map(({label,count})=>[label,count,matches.length,'此地区热榜记录数'])]});
    return section('trends', 'Google Trends · 搜索热度',
      (complete.length ? `<p>${esc(regionLabel)} · ${complete.length} 个有效时间点</p>` +
        (change ? `<p>较早 3 点均值 ${format.format(change.previous)}，最近 3 点均值 ${format.format(change.recent)}。<br>${when(change.from)} 至 ${when(change.to)}</p>` : '') +
        evidence('查看完整热度记录',dataTable('Google Trends 相对热度',['时间（UTC）','相对热度','状态'],
          points.map(p=>[p.timestamp,p.value,p.is_partial?'未完成':p.value==null?'缺失':'有效记录']))) +
        (publicSeries ? `<button type="button" class="secondary mini" data-ga-request="${esc(publicSeries.row.request_id)}">查看原始热度请求</button>` : '')
        : '<p>当前条件下没有可读取的关键词热度曲线。</p>') +
      (matches.length ? `<details open id="gaTrendHotlist"><summary>地区热榜记录</summary>` + records('地区热榜记录',list(matches, ({item, row}) =>
        `${esc(item.topic)} · ${scopeText(row.query_conditions)} · ${when(row.completed_at)} · ${esc(item.approx_traffic_label || '搜索量标记未知')}
         <button type="button" class="secondary mini" data-ga-request="${esc(row.request_id)}">查看原始请求</button>`)) + '</details>'
        : '<p class="ga-meta">最近读取的最多 25 份地区热榜快照没有匹配话题。</p>'));
  }

  function images(data, query) {
    const rows = scopedRows(data, query);
    const items = rows.flatMap(row => (row.items || []).map(item => ({item, row})));
    const counts = new Map();
    for (const {item} of items) {
      const type = item.width && item.height ? (item.width===item.height?'方图':item.width>item.height?'横图':'竖图') : '尺寸未知';
      counts.set(type,(counts.get(type)||0)+1);
    }
    const sizes = [...counts].map(([label,count])=>({label,count}));
    publish('images',{data:rows,chart:sizes.length?reportChart('图片尺寸分布','按已存图片记录的宽高分组。',bars(sizes,items.length),'images'):'',
      metrics:sizes.map(({label,count})=>[label,count,items.length,'图片记录数'])});
    return section('images', 'Google Images · 图片来源', items.length
      ? `<p>${rows.length} 次搜索 · ${items.length} 条图片记录 · 最近采集 ${when(rows[0].completed_at)}</p>` +
        evidence('图片与来源网页', records('图片与来源网页',list(items, ({item, row}) =>
          `${display(item)} · ${item.width && item.height ? item.width + ' × ' + item.height : '尺寸未知'}
           <p>${item.source_page_url ? link(item.source_page_url, '来源网页') : '来源网页未知'}${item.thumbnail_url ? ' · ' + link(item.thumbnail_url, '打开缩略图') : ''}</p>
           <button type="button" class="secondary mini" data-ga-request="${esc(row.request_id)}">查看原始请求</button>`)))
      : '<p>当前条件没有匹配的公开图片快照。</p>');
  }

  async function maps(signal) {
    let saved;
    try { saved = JSON.parse(localStorage.getItem('sf-maps-last') || 'null'); } catch {}
    if (!saved?.id || !saved?.token) return null;
    const res = await fetch('/api/maps/jobs/' + encodeURIComponent(saved.id) + '/results', {
      signal, headers: {'X-SearchFlow-Quick': '1', 'X-Quick-Token': saved.token},
    });
    return res.ok ? res.json() : null;
  }

  function mapReport(snapshot, query, filters) {
    const fallback = '<p>没有匹配当前关键词与地区的已保存商家快照。</p><a href="#maps">打开 Google Maps</a>';
    const p = snapshot?.parameters || {};
    const places = snapshot?.result_cache?.places;
    if (!Array.isArray(places) || !places.length || lower(p.query||p.included_type)!==lower(query) ||
        (filters.country && lower(p.region)!==filters.country) ||
        (filters.language && lower(p.language)!==filters.language)) {
      publish('maps',{data:[]});
      return section('maps', 'Google Maps · 已存商家', fallback);
    }
    const grouped = new Map();
    for (const place of places) {
      const type = place.primary_type || '类型未知';
      grouped.set(type,(grouped.get(type)||0)+1);
    }
    const categories = [...grouped].map(([label,count])=>({label,count})).sort((a,b)=>b.count-a.count);
    publish('maps',{data:{parameters:p, places},
      chart:reportChart('地图商家类型','同城市与半径的已存快照。',bars(categories.slice(0,8),places.length),'maps'),
      metrics:categories.map(({label,count})=>[label,count,places.length,'商家数'])});
    return section('maps', 'Google Maps · 最近保存的商家快照',
      `<p>${esc(p.query || p.included_type || '商家搜索')} · ${esc(p.city || '城市未知')} · 半径 ${esc(p.radius_m ?? '未知')} 米 · ${places.length} 家 · 浏览器最近保存的快照</p>` +
      evidence('商家评分、评价数和距离', records('地图商家',list(places, place =>
        `${link(place.google_maps_uri, place.name || '名称未知')}<p>${esc(place.formatted_address || '地址未知')} · 评分 ${esc(place.rating ?? '未知')} · 评价 ${esc(place.user_rating_count ?? '未知')} · 距中心 ${esc(place.distance_m ?? '未知')} 米</p>`)))
      + '<a href="#maps">打开 Maps 明细与下载</a>');
  }

  function baseCriteria() {
    return {days: $('gaDays').value, region: region.value, language: $('gaLanguage').value,
      device: $('gaDevice').value, domain: $('gaDomain').value.trim(), competitors: $('gaCompetitors').value.trim()};
  }

  let mode = 'keyword';
  let pending = null;
  const drafts = new Map();
  const fields = ['gaInput','gaDays','gaRegion','gaLanguage','gaDevice','gaDomain','gaCompetitors'];
  const visited = new Map();
  function setMode(next) {
    if (mode === next) return;
    controller?.abort();
    pending = null;
    loading('');
    drafts.set(mode,fields.map(id => $(id).value));
    if (applied) {
      if (applied.jobs) {
        for (const [id,state] of applied.states) {
          if (state === 'loading') applied.states.set(id,'error');
        }
        showSourceState(applied);
      }
      result.querySelector('.ga-previous')?.remove();
      const content = document.createDocumentFragment();
      content.append(...result.childNodes);
      visited.set(mode,{report:applied,content});
    }
    const previous = visited.get(next);
    mode = next;
    const values = drafts.get(next) || ['','30','','','','',''];
    fields.forEach((id,index) => { $(id).value = values[index]; });
    input.placeholder = next === 'website' ? '例如 example.com' : '例如 solar energy';
    $('gaInputLabel').textContent = next === 'website' ? '网站域名' : '关键词';
    $('gaExample').textContent = examples[next];
    document.querySelectorAll('[data-ga-keyword-filter]').forEach(element => { element.hidden = next === 'website'; });
    document.querySelectorAll('[data-ga-mode]').forEach(button => {
      button.setAttribute('aria-pressed',String(button.dataset.gaMode === next));
    });
    result.replaceChildren(...(previous ? [previous.content] : []));
    applied = previous?.report || null;
    visited.delete(next);
  }

  function loading(message, busy = false) {
    $('gaProgress').hidden = !busy;
    status.textContent = message;
  }

  function reportHeading(report) {
    const {query, criteria, mode} = report;
    return `<header class="ga-view-heading">${report.example ? '<span class="ga-example-mark">示例 · 已保存的记录</span>' : ''}<h2 id="gaResultHeading">${esc(query)}</h2>
      <p class="ga-meta">近 ${esc(criteria.days)} 天 · ${esc(regionNames[criteria.region] || criteria.region || '全部地区')} · ${report.example && mode === 'website' ? '示例关键词：' + esc(examples.keyword) : '历史快照'}</p></header>`;
  }

  function resultActions(monitor = false) {
    return `<div class="ga-tools ga-report-actions">
      ${monitor ? '<button type="button" class="secondary mini" data-ga-monitor hidden>加入关键词监测</button>' : ''}
      <button type="button" class="secondary mini" data-ga-export="csv" disabled>下载指标 CSV</button>
      <button type="button" class="secondary mini" data-ga-export="json" disabled>下载分析 JSON</button>
    </div>`;
  }

  function showReport(report, html) {
    applied = report;
    result.innerHTML = `<section class="ga-overview ga-report" aria-labelledby="gaResultHeading">
      ${reportHeading(report)}${html}<div id="gaRequestEvidence" hidden role="status"></div></section>`;
  }

  function updateDownloads(report) {
    const csv = result.querySelector('[data-ga-export="csv"]');
    const json = result.querySelector('[data-ga-export="json"]');
    if (csv) csv.disabled = ![...report.outputs.values()].some(output => output.metrics.length);
    if (json) json.disabled = !report.outputs.size;
  }

  function webReport(query, criteria, signal, scope = '') {
    const params = {query, days:criteria.days, region:criteria.region, language:criteria.language,
      domain:criteria.domain, competitors:criteria.competitors, scope};
    const path = '/api/product/analytics';
    return read(path, params, signal).then(data => {
      if (scope) return data;
      const choices = data.scope_choices || [];
      const selected = choices.find(group => group.page === 1 && (!criteria.device || group.device === criteria.device))
        || choices.find(group => !criteria.device || group.device === criteria.device);
      if (criteria.device && !selected) return {samples:0,scope_choices:choices};
      return selected && data.scope_id !== selected.id ? read(path, {...params,scope:selected.id},signal) : data;
    });
  }

  const rank = value => value == null ? '未观测' : '第 ' + value + ' 名';
  const siteScope = scope => {
    const device = {desktop:'桌面端',mobile:'移动端'}[scope.device] || scope.device;
    return [regionNames[scope.region] || scope.region, {en:'英语',zh:'中文'}[scope.language] || scope.language, device]
      .filter(Boolean).map(esc).join(' · ') || '搜索条件未完整记录';
  };
  function siteRow(query, data, domain) {
    const top = data.top10 || {};
    const ranks = top.domains?.find(item => lower(item.domain) === domain);
    const observedRank = data.domains?.find(item => lower(item.domain) === domain)?.latest_rank ?? null;
    return {query, scope:data.scope || {}, samples:top.samples || 0, observed:data.samples || 0,
      hits:ranks?.hits || 0, observedRank, latest:ranks?.latest_rank ?? null, previous:ranks?.previous_rank ?? null, pair:top.pair || []};
  }

  async function websiteReport(report, filters, signal) {
    const {query:domain, criteria} = report;
    // The example deliberately shows one known keyword from the saved report.
    const data = report.example ? {requests:[{query:examples.keyword}]} :
      await read(observationsPath, {...filters,source:'google_web',domain,...(criteria.device && {device:criteria.device})}, signal);
    const terms = [...new Set((data.requests || []).map(row => row.query).filter(Boolean))];
    const rows = [];
    const failed = [];
    for (const query of terms.slice(0, 6)) {
      signal.throwIfAborted();
      try {
        const details = await webReport(query, {...criteria,domain,competitors:''},signal);
        rows.push(siteRow(query,details,domain));
      } catch (error) {
        if (signal.aborted) throw error;
        failed.push(query);
      }
    }
    return {rows, failed, candidates:terms.length, hasMore:Boolean(data.has_more)};
  }

  function siteContent(report, data) {
    const {rows, failed, candidates, hasMore} = data;
    const comparable = rows.filter(row => row.pair.length === 2 && row.latest !== null && row.previous !== null);
    const change = comparable.filter(row => row.latest !== row.previous)
      .sort((a,b) => Math.abs(b.previous-b.latest)-Math.abs(a.previous-a.latest))[0];
    const summary = change ? `<div class="ga-site-highlight"><h3>「${esc(change.query)}」从${rank(change.previous)}变为${rank(change.latest)}</h3>
      <p class="ga-meta">${when(change.pair[1].observed_at)} → ${when(change.pair[0].observed_at)} · ${siteScope(change.scope)}</p></div>`
      : `<p class="ga-meta">${comparable.length ? '可直接对比的关键词没有观察到名次变化。'
        : '排名变化需要两次同条件的完整首页快照。'}</p>`;
    const table = rows.length ? `<div class="ga-table" role="region" aria-label="按关键词查看排名" tabindex="0">
      <table><caption>按关键词查看排名</caption><thead><tr><th scope="col">关键词与条件</th><th scope="col">上一次</th>
      <th scope="col">最近一次</th><th scope="col">进入前 10 名</th></tr></thead><tbody>
      ${rows.map(row => `<tr><th scope="row">
        <button type="button" class="secondary mini" data-ga-keyword="${esc(row.query)}" data-ga-domain="${esc(report.query)}">${esc(row.query)}</button>
        <p class="ga-meta">${siteScope(row.scope)}</p></th>
        <td>${row.pair.length > 1 ? rank(row.previous) : '—'}</td><td>${row.samples ? rank(row.latest) : row.observedRank !== null ? rank(row.observedRank) + '（单次观测）' : '—'}</td>
        <td>${row.samples ? row.hits + '/' + row.samples + ' 次' : '样本不足'}</td></tr>`).join('')}</tbody></table></div>` : '';
    const references = rows.filter(row => row.pair.length).map(row =>
      evidence(row.query + ' · 原始搜索',row.pair.map(sample =>
        `<button type="button" class="secondary mini" aria-expanded="false" data-ga-request="${esc(sample.request_id)}">查看 ${when(sample.observed_at)} 的快照</button>`).join(' '))).join('');
    const tracked = change || rows.find(row => row.latest !== null || row.observedRank !== null) || rows[0];
    const count = rows.reduce((sum,row) => sum+row.samples,0);
    return `<div class="ga-site-layout">
      <div>${rows.length ? summary + table : failed.length ? '' : '<p>当前范围内没有找到包含这个域名的公开网页快照。可以扩大时间范围，或用已知关键词查看排名。</p>'}
      ${references}</div>
      <section class="ga-site-summary"><h3>样本概况</h3>
        <dl><div><dt>关键词</dt><dd>${rows.length} 个</dd></div><div><dt>完整首页快照</dt><dd>${count} 次</dd></div></dl>
        ${hasMore || candidates > 6 ? '<p class="ga-meta">从最近匹配记录中核对最多 6 个关键词。</p>' : ''}
        ${tracked ? `<button type="button" class="secondary mini" data-ga-monitor="${esc(report.query)}"
          data-ga-monitor-query="${esc(tracked.query)}" data-ga-monitor-region="${esc(tracked.scope.region || '')}"
          data-ga-monitor-language="${esc(tracked.scope.language || '')}">跟踪「${esc(tracked.query)}」</button>` : ''}
      </section></div>
      ${failed.length ? '<p class="ga-meta">有 '+failed.length+' 个关键词暂时无法读取。 <button type="button" class="secondary mini" data-ga-retry="report">重试</button></p>' : ''}
      ${evidence('怎么统计的','<p class="ga-meta">前 10 名仅统计完整首页，重复缓存不计。单次观测表示最近已知名次。</p>')}
      ${resultActions()}`;
  }

  const sourceNames = {web:'网页排名',news:'新闻报道',trends:'搜索热度',images:'Google 图片',maps:'Google 地图'};
  const primarySources = ['web','news','trends'];

  function keywordContent() {
    return `<div class="ga-keyword-layout">
      <div><h3>主要发现</h3><ul class="ga-findings" id="gaFindings"></ul>
      <div id="gaSourceStates"></div></div><div id="gaLeadChart" hidden></div></div>
      ${resultActions(true)}
      <details open class="ga-disclosure" id="gaAllData"><summary>图表和原始证据</summary>
      ${Object.entries(sourceNames).map(([id,label]) => `<details open class="ga-source-details" id="gaDetail-${id}" hidden><summary>${label}</summary>
        <div class="ga-charts" id="gaChart-${id}"></div><div id="gaSource-${id}" tabindex="-1"></div></details>`).join('')}</details>`;
  }

  function showSourceState(report) {
    const items = primarySources.flatMap(id => {
      const state = report.states.get(id);
      const finding = report.outputs.get(id)?.finding;
      if (state === 'error') return [];
      if (!finding) return state === 'loading' ? [`<li class="ga-pending-source"><span>${sourceNames[id]}正在读取…</span></li>`] : [];
      return [`<li><div><strong>${esc(finding.title)}</strong><p class="ga-meta">${esc(finding.detail)}</p></div>
      <button type="button" class="secondary mini" data-ga-jump="${esc(finding.target)}">查看依据</button></li>`];
    });
    const errors = Object.keys(sourceNames).filter(id => report.states.get(id) === 'error');
    $('gaFindings').innerHTML = items.join('');
    $('gaSourceStates').innerHTML = errors.map(id => `<p class="ga-meta">${sourceNames[id]}暂时读不到。 <button type="button" class="secondary mini" data-ga-retry="${id}">重试</button></p>`).join('');
    const monitor = result.querySelector('[data-ga-monitor]');
    if (monitor) monitor.hidden = !report.web?.samples;
    updateDownloads(report);
  }

  async function readSource(report, id, job, signal) {
    try {
      const data = await job.read(signal);
      if (signal.aborted || (pending !== report && applied !== report)) return;
      if (applied !== report) showReport(report,keywordContent());
      if (id === 'web') report.web = data;
      const html = job.render(data);
      const supplementary = !primarySources.includes(id);
      if (!supplementary || report.outputs.get(id)?.metrics.length) {
        $('gaDetail-'+id).hidden = false;
        $('gaSource-'+id).innerHTML = html;
      }
      report.states.set(id,'ready');
    } catch {
      if ((signal.aborted && signal.reason !== 'timeout') || (pending !== report && applied !== report)) return;
      report.states.set(id,'error');
    }
    if (applied === report) showSourceState(report);
  }

  async function analyze(text, criteria = baseCriteria(), {scope = '', reportMode = mode, example = false} = {}) {
    const query = reportMode === 'website' ? hostname(text) : text;
    if (!query) { loading('请输入有效的网站域名，例如 example.com。'); return; }
    startedModes.add(reportMode);
    controller?.abort();
    controller = new AbortController();
    const {signal} = controller;
    const cutoff = new Date();
    cutoff.setUTCHours(0, 0, 0, 0);
    cutoff.setUTCDate(cutoff.getUTCDate() - Number(criteria.days) + 1);
    const filters = {from:cutoff.toISOString(),limit:'30',
      ...(criteria.region && {country:lower(criteria.region)}), ...(criteria.language && {language:lower(criteria.language)})};
    const report = {query, criteria, scope, mode:reportMode, example, from:filters.from, outputs:new Map(), states:new Map()};
    pending = report;
    result.querySelector('.ga-previous')?.remove();
    if (applied) {
      const label = document.createElement('p');
      label.className = 'ga-meta ga-previous';
      label.textContent = '上一份报告';
      $('gaResultHeading').before(label);
    } else {
      result.innerHTML = '<div class="ga-report ga-skeleton" aria-hidden="true"><div></div><div></div><div></div></div>';
    }
    loading('正在读取' + (example ? '示例' : '「' + query + '」') + '…',true);
    const timeout = setTimeout(() => { if (controller.signal === signal) controller.abort('timeout'); },30000);
    try {
      if (reportMode === 'website') {
        const data = await websiteReport(report,filters,signal);
        if (signal.aborted || pending !== report) return;
        report.outputs.set('web',{data,metrics:data.rows.flatMap(row => row.samples ? [
          [row.query + ' · 进入前 10 名',row.hits,row.samples,siteScope(row.scope)],
          [row.query + ' · 最近名次',row.latest,null,siteScope(row.scope)],
          [row.query + ' · 上一次名次',row.previous,null,siteScope(row.scope)],
        ] : row.observedRank !== null ? [[row.query + ' · 单次观测名次',row.observedRank,null,siteScope(row.scope)]] : [])});
        showReport(report,siteContent(report,data));
        updateDownloads(report);
      } else {
        const exact = {...filters,query,query_match:'exact'};
        report.jobs = {
          web:{read:s => webReport(query,criteria,s,scope),render:data => web(data,criteria)},
          news:{read:s => read(observationsPath,{...exact,source:'google_news'},s),render:data => news(data,query)},
          trends:{read:s => Promise.all([
            read(observationsPath,{...exact,source:'google_trends',query:'trending-now',limit:'25'},s),
            read(observationsPath,{...exact,source:'google_trends',limit:'5'},s),
            savedTrend(query,filters,s).catch(() => null),
          ]),render:data => trends(data,query,filters.from)},
          images:{read:s => read(observationsPath,{...exact,source:'google_images',limit:'12'},s),render:data => images(data,query)},
          maps:{read:maps,render:data => mapReport(data,query,filters)},
        };
        for (const id of Object.keys(report.jobs)) report.states.set(id,'loading');
        const jobs = Object.entries(report.jobs);
        await Promise.all(jobs.filter(([id]) => primarySources.includes(id)).map(([id,job]) => readSource(report,id,job,signal)));
        if (applied === report) await Promise.all(jobs.filter(([id]) => !primarySources.includes(id)).map(([id,job]) => readSource(report,id,job,signal)));
      }
    } catch {
      // A failed read leaves the previous report interactive.
    } finally {
      clearTimeout(timeout);
      if (pending === report) {
        pending = null;
        if (applied === report) loading('');
        else {
          if (!applied) result.replaceChildren();
          loading('暂时无法读取「' + query + '」。' + (applied ? '下方仍是上一份报告。' : '请点击「查看分析」重试。'));
        }
      }
    }
  }

  document.querySelectorAll('[data-ga-mode]').forEach(button => {
    button.onclick = () => {
      setMode(button.dataset.gaMode);
      loadInitialExample();
    };
  });
  $('gaForm').addEventListener('submit', event => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) return;
    if (mode === 'keyword' && hostname(value)) {
      setMode('website');
      input.value = value;
    }
    analyze(value);
  });
  function loadExample() {
    const query = examples[mode];
    fields.forEach((id,index) => { $(id).value = [query,'90','','','','',''][index]; });
    analyze(query,baseCriteria(),{example:true});
  }
  function loadInitialExample() {
    if (!startedModes.has(mode) && !input.value.trim() && !pending && !applied) loadExample();
  }
  $('gaExample').onclick = loadExample;
  result.addEventListener('change', event => {
    if (event.target.id === 'gaScope' && applied)
      analyze(applied.query, applied.criteria, {scope:event.target.value,example:applied.example});
  });

  function exportAnalysis(format) {
    const {query, criteria, scope, from, outputs} = applied;
    if (format==='json') {
      const sources = Object.fromEntries(outputs);
      const report = {query,mode:applied.mode,criteria,scope:applied.web?.scope_id||scope,from,generated_at:new Date().toISOString(),
        methodology:'基于已保存快照。网页按同条件比较，新闻按去重文本统计，热度使用 0–100 相对指数。',
        findings:[...outputs.values()].map(output => output.finding).filter(Boolean),sources};
      saveBlob(new Blob([JSON.stringify(report,null,2)],{type:'application/json;charset=utf-8'}),'google-analysis.json');
      return;
    }
    const cell = value => {
      let text = String(value??'');
      if (/^[=+@-]/.test(text.trimStart())) text="'"+text;
      return '"'+text.replace(/"/g,'""')+'"';
    };
    const rows = [['查询','来源','指标','值','分母','口径','地区筛选','观测时间起点（UTC）']];
    for (const [source,{metrics}] of outputs) {
      for (const metric of metrics) rows.push([query,source,...metric,criteria.region||'全部',from]);
    }
    saveBlob(new Blob(['\uFEFF'+rows.map(row=>row.map(cell).join(',')).join('\r\n')],
      {type:'text/csv;charset=utf-8'}),'google-analysis.csv');
  }

  let evidenceRead = 0;
  result.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.gaRetry && applied) {
      if (pending && pending !== applied) return;
      const report = applied;
      const id = button.dataset.gaRetry;
      if (id === 'report') { await analyze(report.query,report.criteria,{example:report.example}); return; }
      const job = report.jobs?.[id];
      if (!job) return;
      button.disabled = true;
      button.textContent = '正在重试…';
      const retry = new AbortController();
      const timeout = setTimeout(() => retry.abort('timeout'),20000);
      try { await readSource(report,id,job,retry.signal); }
      finally { clearTimeout(timeout); }
    } else if (button.dataset.gaJump) {
      const target = $(button.dataset.gaJump);
      if (!target) return;
      $('gaAllData').open=true;
      const source = target.closest('.ga-source-details');
      if (source) source.open = true;
      if (target instanceof HTMLDetailsElement) target.open=true;
      const focus = target instanceof HTMLDetailsElement ? target.querySelector('summary') : target;
      focus.focus({preventScroll:true});
      focus.scrollIntoView({block:'start'});
    } else if (button.dataset.gaExport && applied) {
      exportAnalysis(button.dataset.gaExport);
    } else if (button.dataset.gaKeyword) {
      setMode('keyword');
      input.value = button.dataset.gaKeyword;
      $('gaDomain').value = button.dataset.gaDomain;
      analyze(input.value);
    } else if (button.hasAttribute('data-ga-monitor') && applied) {
      const draft = ['monitorQuery','monitorDomain','monitorCompetitors'].some(id => $(id).value.trim());
      if (draft) {
        status.textContent = '监测表单里已有草稿。打开关键词监测可继续编辑。';
        return;
      }
      const scope = applied.web?.scope || {};
      $('monitorQuery').value = button.dataset.gaMonitorQuery || applied.query;
      const domain = button.dataset.gaMonitor || applied.web?.auto_domains?.[0] || '';
      $('monitorDomain').value = domain;
      $('monitorCompetitors').value = applied.criteria.competitors.replace(/，/g, ',');
      for (const [id, value] of [['monitorRegion', button.dataset.gaMonitorRegion || scope.region || applied.criteria.region], ['monitorLanguage', button.dataset.gaMonitorLanguage || scope.language || applied.criteria.language]]) {
        const select = $(id);
        const option = [...select.options].find(o => lower(o.value) === lower(value));
        select.selectedIndex = option ? option.index : 0;
      }
      window.addEventListener('hashchange', () => {
        $('monitorForm').scrollIntoView({block:'start'});
        $(domain ? 'monitorQuery' : 'monitorDomain').focus({preventScroll:true});
      }, {once:true});
      location.hash = 'monitors';
    } else if (button.dataset.gaRequest) {
      const id = button.dataset.gaRequest;
      const attempt = ++evidenceRead;
      const target = $('gaRequestEvidence');
      const previous = result.querySelector('[data-ga-request][aria-expanded="true"]');
      previous?.setAttribute('aria-expanded', 'false');
      previous?.removeAttribute('aria-controls');
      if (previous === button) {
        target.hidden = true;
        return;
      }
      button.setAttribute('aria-expanded', 'true');
      button.setAttribute('aria-controls', target.id);
      (button.closest('details, .ga-section') || button.parentElement).after(target);
      target.hidden = false;
      target.textContent = '正在读取证据…';
      const current = () => evidenceRead === attempt && target.isConnected && button.getAttribute('aria-expanded') === 'true';
      try {
        const {request} = await read(requestPath + encodeURIComponent(id), null);
        if (!current()) return;
        const original = (request.items || []).map(item => item.kind === 'image'
          ? '<p>' + display(item) + ' · ' + link(item.source_page_url, '来源网页') + '</p>'
          : item.kind === 'keyword_interest_over_time' ? qTrendChart(item) : grHistoryItem(item));
        target.innerHTML = section('request', '原始请求证据',
          `<p>${esc(request.query || '关键词未知')} · ${when(request.completed_at)} · ${scopeText(request.query_conditions)} · ${esc(id)}</p>${original.join('')}`);
      } catch (error) {
        if (current()) target.textContent = '证据暂时无法读取。收起后可以重新打开。';
      }
    }
  });

  advanced.addEventListener('toggle', () => {
    if (view === 'analysis' && advanced.open && !analysisLoaded &&
        $('analysisView').getAttribute('aria-busy') !== 'true') loadAnalysis().catch(error => notice(error.message,true));
  });
  function loadOpenHistory() {
    if (view !== 'analysis' || !history.open || historyRequested) return;
    historyRequested = true;
    loadGoogleResearch().catch(error => {
      historyRequested = false;
      notice(error.message,true);
    });
  }
  history.addEventListener('toggle',loadOpenHistory);
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
      $('pageSubtitle').textContent = '查看已保存的搜索变化与依据';
      legacyHistory.hidden = false;
      loadInitialExample();
      loadOpenHistory();
    } else if (view === 'insights') {
      $('pageTitle').textContent = '文本分析';
    }
  };
})();
