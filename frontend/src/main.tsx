import React, { useEffect, useMemo, useState, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import { Button, ButtonLink, Chip, StatCards, NavItem, Table, TableHeader, TableColumn, TableBody, TableRow, TableCell, SegmentedControl } from './components';
import { Task, TaskTrigger, TaskContent, TaskItem } from './vendor/ai-elements/task';
import { LayoutDashboard as RiDashboardLine, ListTodo as RiPlayList2Line, Database as RiDatabase2Line, MapPin as RiMapPin2Line, Search as RiSearchLine, ChartBar as RiBarChartBoxLine, ChartLine as RiLineChartLine, Radar as RiRadarLine, Code as RiCodeSSlashLine, Layers as RiStackLine, ArrowLeftRight as RiExchangeLine, Settings2 as RiSettings4Line, ArrowUpRight as RiArrowRightUpLine, ArrowRight as RiArrowRightLine, Plus as RiAddLine, PanelLeftClose as RiSidebarFoldLine, PanelLeftOpen as RiSidebarUnfoldLine, Download as RiDownload2Line, FileText as RiFileList3Line, Check as RiCheckLine, LoaderCircle, Circle, CircleAlert } from 'lucide-react';

const byId = (id: string) => document.getElementById(id);
const text = (el: Element | null | undefined) => el?.textContent?.trim() || '';
function mount(id: string, at: Element, where: 'before' | 'after' | 'append' = 'append') {
  const el = document.createElement('div'); el.id = id; el.className = 'sf-island';
  if (where === 'before') at.before(el); else if (where === 'after') at.after(el); else at.append(el);
  return createRoot(el);
}
function useDOM<T>(node: Node | null, read: () => T, options: MutationObserverInit = {childList:true, subtree:true, characterData:true, attributes:true}) {
  const [value, setValue] = useState(read);
  useEffect(() => {
    if (!node) return;
    let queued = false;
    const observer = new MutationObserver(() => {
      if (queued) return; queued = true;
      queueMicrotask(() => { queued = false; setValue(read()); });
    });
    observer.observe(node, options); setValue(read());
    return () => observer.disconnect();
  }, [node]);
  return value;
}
const activeView = () => location.hash.slice(1).split('?')[0] || 'overview';
const groups = [
  {label:'数据工作台',items:[['overview','首页概览',RiDashboardLine],['tasks','采集任务',RiPlayList2Line],['results','数据集',RiDatabase2Line]]},
  {label:'分析与监测',items:[['analysis','Google 分析',RiLineChartLine],['insights','文本分析',RiBarChartBoxLine],['monitors','关键词监测',RiRadarLine]]},
  {label:'开发与管理',items:[['api','快速使用',RiPlayList2Line],['developer','API 工具',RiCodeSSlashLine]]}
] as const;
function Navigation() {
  const [current, setCurrent] = useState(activeView);
  const [collapsed, setCollapsed] = useState(() => {try{return localStorage.getItem('sfBoardNav')==='compact'}catch{return false}});
  useEffect(() => {const update = () => setCurrent(activeView()); window.addEventListener('hashchange',update); return () => window.removeEventListener('hashchange',update)},[]);
  useEffect(() => {document.body.classList.toggle('sf-board-compact',collapsed);try{localStorage.setItem('sfBoardNav',collapsed?'compact':'full')}catch{}},[collapsed]);
  return <><div className="sf-nav-groups">{groups.map(group => <div className="sf-nav-group" key={group.label}><div className="sf-nav-label">{group.label}</div>{group.items.map(([key,label,icon]) => <NavItem key={key} viewKey={key} href={'#'+key} icon={icon} label={label} collapsed={collapsed} isSelected={current===key || current==='create'&&key==='tasks'}/>)}</div>)}</div><Button className="sf-nav-collapse" variant="secondary" leadingIcon={collapsed?RiSidebarUnfoldLine:RiSidebarFoldLine} onClick={()=>setCollapsed(!collapsed)} aria-label={collapsed?'展开侧栏':'收起侧栏'} aria-expanded={!collapsed}>{collapsed?undefined:'收起侧栏'}</Button></>;
}
function DataFlow() {
  const diagram=useRef<HTMLDivElement>(null);
  const [size,setSize]=useState({w:1000,h:312});
  const [motion,setMotion]=useState(()=>!matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(()=>{const el=diagram.current;if(!el)return;const update=()=>{const r=el.getBoundingClientRect();if(r.width>0)setSize({w:r.width,h:r.height})};const observer=new ResizeObserver(update);observer.observe(el);update();return()=>observer.disconnect()},[]);
  useEffect(()=>{const media=matchMedia('(prefers-reduced-motion: reduce)');const update=()=>setMotion(!media.matches);media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[]);
  const sources=[{id:'google_web',name:'Google Search',short:'Search'},{id:'google_news',name:'Google News',short:'News'},{id:'google_images',name:'Google Images',short:'Images'},{id:'google_trends',name:'Google Trends',short:'Trends'},{id:'google_maps',name:'Google Maps',short:'Maps'}];
  const logo=(id:string)=>(JSON.parse(byId('sfSourceData')?.textContent||'[]') as Source[]).find(source=>source.id===id)?.logo;
  const mobile=size.w<700,w=size.w,h=size.h;
  const request={x:mobile?w*.5:w*.065,y:mobile?24:h/2};
  const collector={x:mobile?w*.5:w*.255,y:mobile?108:h/2};
  const middle={x:mobile?w*.5:w*.75,y:mobile?316:h/2};
  const result={x:mobile?w*.5:w*.94,y:mobile?400:h/2};
  const points=sources.map((_,i)=>({x:mobile?w*(i+.5)/5:w*.5,y:mobile?212:40+i*58}));
  const curve=(a:{x:number,y:number},b:{x:number,y:number})=>mobile?`M ${a.x} ${a.y} C ${a.x} ${(a.y+b.y)/2} ${b.x} ${(a.y+b.y)/2} ${b.x} ${b.y}`:`M ${a.x} ${a.y} C ${(a.x+b.x)/2} ${a.y} ${(a.x+b.x)/2} ${b.y} ${b.x} ${b.y}`;
  const paths=[{d:curve(request,collector),begin:.05,dur:1},...points.map((p,i)=>({d:curve(collector,p),begin:1+i*.12,dur:1.6})),...points.map((p,i)=>({d:curve(p,middle),begin:2.6+i*.12,dur:1.6})),{d:curve(middle,result),begin:4.8,dur:1}];
  const stage=(point:{x:number,y:number},name:string,Icon:typeof RiSearchLine,detail:string)=><div className="sf-route-stage" style={{left:point.x,top:point.y}}><span><Icon/></span><strong>{name}</strong><small>{detail}</small></div>;
  return <div className="sf-flow sf-route-flow"><div className="sf-flow-head"><div><span className="sf-eyebrow">SEARCHFLOW WORKSPACE</span><h2>从请求到数据，一条完整链路</h2><p>采集器按来源获取数据，汇总到中台，再返回结果。</p></div></div>
    <div ref={diagram} className="sf-route-diagram" role="img" aria-label="目标流程示意：用户请求进入采集器，采集器访问五种 Google 来源，结果汇总至数据中台，再返回用户。当前仅 Google Web 已接通中台，其余来源独立返回。">
      <svg className="sf-route-svg" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">{paths.map((path,i)=><g key={i}><path id={'sfRoute'+i} d={path.d}/>{motion&&<circle className="sf-route-dot" r="3.2"><animateMotion dur="6.6s" keyTimes={`0;${path.begin/6.6};${(path.begin+path.dur)/6.6};1`} keyPoints="0;0;1;1" calcMode="linear" repeatCount="indefinite"><mpath href={'#sfRoute'+i}/></animateMotion><animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes={`0;${path.begin/6.6};${(path.begin+.01)/6.6};${(path.begin+path.dur-.01)/6.6};${(path.begin+path.dur)/6.6};1`} dur="6.6s" repeatCount="indefinite"/></circle>}</g>)}</svg>
      {stage(request,'发出请求',RiSearchLine,'关键词 · 地区')}{stage(collector,'采集器',RiStackLine,'按来源采集')}
      {points.map((point,i)=><div key={sources[i].id} className="sf-route-source" style={{left:point.x,top:point.y,width:mobile?w/5-4:154}}>{logo(sources[i].id)&&<img src={logo(sources[i].id)} alt=""/>}<strong>{mobile?sources[i].short:sources[i].name}</strong></div>)}
      {stage(middle,'数据中台',RiDatabase2Line,'汇总 · 存储')}{stage(result,'返回结果',RiDownload2Line,'CSV / JSONL')}
    </div>
    <div className="sf-flow-foot"><span><i/>目标流程示意</span><span>Web 已经中台返回；News、Images、Trends、Maps 当前独立返回，中台汇总待接入。</span></div>
  </div>;
}

function OverviewStats({original}: {original: HTMLElement}) {
  const data = useDOM(original, () => ({stale:original.dataset.stale==='true',stats:[...original.children].filter(x=>x.querySelector('strong')).map((x,i)=>({icon:[RiFileList3Line,RiDatabase2Line,RiSearchLine,RiPlayList2Line][i]||RiDatabase2Line,label:text(x.querySelector('span')),value:text(x.querySelector('strong')),delta:i===3?'采集时间':'公开数据',deltaColor:'neutral' as const}))}));
  if(data.stats.length<4&&data.stale)return <div className="report-error" role="status"><p>数据汇总暂时无法读取</p><button type="button" className="secondary mini" data-archive-retry>重新加载</button></div>;
  if(data.stats.length<4&&!data.stale)return <div className="sf-stats-skeleton" role="status" aria-label="正在读取数据汇总">{[0,1,2,3].map(i=><div key={i}><span/><b/><small/></div>)}</div>;
  return <div className={'sf-overview-stats'+(data.stale?' sf-data-stale':'')}><StatCards stats={data.stats.length?data.stats:[{icon:RiDatabase2Line,label:'正在读取历史汇总',value:'—',delta:'公开数据',deltaColor:'neutral'}]} className="sf-stat-grid"/>{data.stale&&<p role="status">汇总暂未刷新；已有数字为上次读取结果。</p>}</div>;
}
type Source = {id:string; label:string;description:string;logo?:string};
function Sources({sources}: {sources:Source[]}) {
  return <div className="sf-source-grid">{sources.map(source => <a href={'#'+(source.id==='google_maps'?'api':'results')} key={source.id} data-open-source={source.id==='google_maps'?undefined:source.id} onClick={source.id==='google_maps'?()=>document.querySelector<HTMLButtonElement>('[data-quick-mode="maps"]')?.click():undefined} className="sf-source-card"><span className="sf-source-logo">{source.logo?<img src={source.logo} alt=""/>:source.id==='google_maps'?<RiMapPin2Line/>:source.id==='google_news'?<RiFileList3Line/>:source.id==='google_trends'?<RiLineChartLine/>:source.id==='google_images'?<RiStackLine/>:<RiSearchLine/>}</span><div><h3>{source.label}</h3><p>{source.description}</p></div><RiArrowRightUpLine className="sf-source-arrow"/></a>)}</div>;
}
const modeOptions = [{value:'live',label:'采集新数据',icon:<RiSearchLine/>},{value:'maps',label:'地图商家',icon:<RiMapPin2Line/>},{value:'history',label:'历史数据',icon:<RiDatabase2Line/>},{value:'record',label:'按 ID 读取',icon:<RiCodeSSlashLine/>}];
function RequestModes({original}: {original:HTMLElement}) {
  const state = useDOM(original, () => ({mode:original.querySelector('[aria-pressed=true]')?.getAttribute('data-quick-mode')||'live',busy:!!(byId('quickSubmit') as HTMLButtonElement)?.disabled}));
  // A busy run also changes the original submit button outside the mode group.
  const busy = useDOM(byId('quickSubmit'),()=>!!(byId('quickSubmit') as HTMLButtonElement)?.disabled,{attributes:true,attributeFilter:['disabled']});
  return <div className="sf-request-modes"><SegmentedControl options={modeOptions.map((o,i)=>({...o,ariaLabel:o.label,label:<><span className="sf-mode-full">{o.label}</span><span className="sf-mode-short">{['新数据','地图','历史','按 ID'][i]}</span></>}))} value={state.mode} size="lg" fullWidth variant="tray" disabled={busy} aria-label="选择请求方式" onChange={value=>(original.querySelector(`[data-quick-mode="${value}"]`) as HTMLButtonElement)?.click()}/><p className="sf-mode-hint">{({live:'选择一个或多个来源，输入关键词后获取新数据。',maps:'按关键词或附近类型查找商家，获取电话、网站与评分。',history:'筛选已保存的数据，直接查看并下载完整记录。',record:'粘贴公开请求 ID，读取完整响应及来源字段。'} as Record<string,string>)[state.mode]}</p></div>;
}
function RequestProgress({status,queue,steps}: {status:HTMLElement;queue:HTMLElement;steps:HTMLElement}) {
  const state = useDOM(status,()=>({message:text(status),error:status.dataset.error==='true',hidden:status.hidden}));
  const current = useDOM(steps,()=>Number(steps.querySelector('[aria-current=step]')?.getAttribute('data-quick-step')||1));
  const entries = useDOM(queue,()=>queue.hidden?[]:[...queue.children].map((row,i)=>({id:String(i),label:text(row),status:/失败|取消|未执行/.test(text(row))?'error':/已完成/.test(text(row))?'done':'running'})));
  const selected = useDOM(document.querySelector('.quick-modes'),()=>document.querySelector('.quick-modes [aria-pressed=true]')?.getAttribute('data-quick-mode'));
  const busy = current===2&&!state.error;
  const progress = [{label:'设置条件',done:current>1},{label:'获取数据',done:current>2},{label:'查看与下载',done:current>3}];
  if (selected==='maps'||state.hidden) return null;
  return <div className={'sf-request-progress'+(state.error?' sf-progress-error':'')} role="status" aria-live="polite"><Task defaultOpen={false}><TaskTrigger title={state.message}><button type="button" className="sf-task-trigger">{state.error?<CircleAlert/>:busy?<LoaderCircle className="sf-spin"/>:current>=3?<RiCheckLine/>:<Circle/>}<span>{state.message}</span><span className="sf-task-toggle">查看流程</span></button></TaskTrigger><TaskContent><div className="sf-task-stages">{progress.map((s,i)=><TaskItem key={s.label} className="sf-task-stage">{s.done?<RiCheckLine/>:<span className="sf-stage-number">{i+1}</span>}<span>{s.label}</span></TaskItem>)}</div>{entries.map(e=><TaskItem key={e.id} className="sf-task-entry">{e.status==='done'?<RiCheckLine/>:e.status==='error'?<CircleAlert/>:<LoaderCircle className="sf-spin"/>}<span>{e.label}</span></TaskItem>)}</TaskContent></Task></div>;

}
type ResultRow = {id:string;title:string;url:string;source:string;meta:string;summary:string;button?:HTMLButtonElement};
function readResultRows(original:HTMLElement):ResultRow[] { return [...original.querySelectorAll<HTMLElement>(':scope > .result')].map((row,i)=>({id:String(i),title:text(row.querySelector('h3')),url:row.querySelector<HTMLAnchorElement>('h3 a')?.href||'',source:text(row.querySelector('.result-meta .badge')),meta:[...row.querySelectorAll('.result-meta>span')].map(text).join(' · '),summary:text(row.querySelector('p')).replace(/^搜索摘要/,''),button:row.querySelector<HTMLButtonElement>('[data-saved]')||undefined})); }
function ResultTable({original}: {original:HTMLElement}) {
  const rows = useDOM(original,()=>readResultRows(original),{childList:true});
  const special = useDOM(original,()=>!!original.querySelector('.images,.trends'),{childList:true});
  const [mode,setMode]=useState('table');
  useEffect(()=>{original.classList.toggle('sf-original-hidden',mode==='table'&&!special&&rows.length>0);return()=>original.classList.remove('sf-original-hidden')},[mode,special,rows.length]);
  return <><div className="sf-table-toolbar"><span>{rows.length?`当前页 ${rows.length} 条记录`:'结果按实际返回展示'}</span><SegmentedControl size="sm" value={special?'list':mode} aria-label="数据集显示方式" options={[{value:'table',label:'表格',disabled:special},{value:'list',label:'详情列表'}]} onChange={setMode}/></div>{mode==='table'&&!special&&rows.length>0&&<><div className="sf-desktop-table"><Table aria-label="已保存的搜索数据"><TableHeader><TableColumn isRowHeader>标题与摘要</TableColumn><TableColumn>来源与采集时间</TableColumn><TableColumn>操作</TableColumn></TableHeader><TableBody items={rows}>{row=><TableRow id={row.id}><TableCell><a className="sf-result-title" href={row.url||undefined} target="_blank" rel="noopener noreferrer">{row.title}</a><p className="sf-table-summary">{row.summary}</p></TableCell><TableCell><Chip color="soft" variant="caption">{row.source}</Chip><p className="sf-table-meta">{row.meta.replace(row.source,'')}</p></TableCell><TableCell><Button variant="secondary" size="small" onClick={()=>row.button?.click()}>查看详情</Button></TableCell></TableRow>}</TableBody></Table></div><div className="sf-mobile-cards">{rows.map(row=><article key={row.id} className="sf-data-card"><Chip color="soft" variant="caption">{row.source}</Chip><h3><a href={row.url||undefined} target="_blank" rel="noopener noreferrer">{row.title}</a></h3><p>{row.summary}</p><Button size="small" variant="secondary" onClick={()=>row.button?.click()}>查看详情</Button></article>)}</div></>}</>;
}
type Place = {name?:string;formatted_address?:string;primary_type?:string;rating?:number|null;user_rating_count?:number|null;distance_m?:number;national_phone_number?:string;website_uri?:string;google_maps_uri?:string;regular_opening_hours?:unknown};
declare global {interface Window { sfComponentBridge: {maps:Place[]; version:string}; }}
function safeURL(value?:string) {try{const url=new URL(value||'');return ['https:','http:'].includes(url.protocol)?url.href:undefined}catch{return undefined}}
function MapsResults({original}: {original:HTMLElement}) {
  const [rows,setRows]=useState<Place[]>(()=>window.sfComponentBridge?.maps||[]);
  const [mode,setMode]=useState('table');
  const [query,setQuery]=useState('');
  useEffect(()=>{const update=()=>{setRows(window.sfComponentBridge.maps);setQuery('')};window.addEventListener('sf:maps-results',update);return()=>window.removeEventListener('sf:maps-results',update)},[]);
  useEffect(()=>{original.classList.toggle('sf-original-hidden',rows.length>0&&mode==='table');return()=>original.classList.remove('sf-original-hidden')},[rows.length,mode]);
  const filtered=useMemo(()=>rows.map((row,i)=>({...row,id:String(i)})).filter(row=>(row.name+' '+row.formatted_address).toLowerCase().includes(query.toLowerCase())),[rows,query]);
  if (!rows.length) return null;
  const rating=(row:Place)=>row.rating==null?'评分未知':`${row.rating} ★ · ${row.user_rating_count==null?'评价数未知':row.user_rating_count+' 条评价'}`;
  const actions=(row:Place)=><div className="sf-map-links">{safeURL(row.website_uri)&&<a href={safeURL(row.website_uri)} target="_blank" rel="noopener noreferrer">网站 ↗</a>}{safeURL(row.google_maps_uri)&&<a href={safeURL(row.google_maps_uri)} target="_blank" rel="noopener noreferrer">地图 ↗</a>}</div>;
  return <div className="sf-maps-data"><div className="sf-table-toolbar"><div><h3>商家数据</h3><span>本次返回 {rows.length} 家 · 按距离排序</span></div><SegmentedControl size="sm" value={mode} options={[{value:'table',label:'表格'},{value:'cards',label:'详情卡片'}]} aria-label="商家结果显示方式" onChange={setMode}/></div><label className="sf-local-filter" hidden={mode!=='table'}><RiSearchLine/><input type="search" value={query} onChange={e=>setQuery(e.target.value)} aria-label="筛选本次商家" placeholder="筛选本次商家的名称或地址"/></label>{mode==='table'?<><div className="sf-desktop-table"><Table aria-label="Google Maps 商家结果"><TableHeader><TableColumn isRowHeader>商家 / 地址</TableColumn><TableColumn>评分与距离</TableColumn><TableColumn>联系电话</TableColumn><TableColumn>链接</TableColumn></TableHeader><TableBody items={filtered} renderEmptyState={()=>'没有匹配商家，请修改筛选词。'}>{row=><TableRow id={row.id}><TableCell><strong>{row.name||'名称未知'}</strong><p className="sf-table-meta">{row.formatted_address||'地址未知'}</p><span className="sf-table-meta">{row.primary_type||'类型未知'}</span></TableCell><TableCell><span>{rating(row)}</span><p className="sf-table-meta">{row.distance_m==null?'距离未知':`距中心 ${(row.distance_m/1000).toFixed(1)} 公里`}</p></TableCell><TableCell>{row.national_phone_number||'电话未知'}</TableCell><TableCell>{actions(row)}</TableCell></TableRow>}</TableBody></Table></div><div className="sf-mobile-cards">{filtered.map(row=><article key={row.id} className="sf-data-card"><h3>{row.name||'名称未知'}</h3><p>{row.formatted_address||'地址未知'}</p><p>{rating(row)} · {row.distance_m==null?'距离未知':(row.distance_m/1000).toFixed(1)+' 公里'}</p><p>{row.national_phone_number||'电话未知'}</p>{actions(row)}</article>)}{!filtered.length&&<p>没有匹配商家，请修改筛选词。</p>}</div></>:<p className="sf-card-note">完整营业时间与商家资料见下方卡片。</p>}<p className="sf-table-note">来源：Google Maps · 自建采集；表内筛选仅影响预览，下载保留完整结果。</p></div>;
}
function QuickStart() {return <div className="sf-start-card"><span className="sf-start-icon"><RiCodeSSlashLine/></span><div><strong>从一个请求开始</strong><p>选择数据来源，设置查询条件，直接查看并下载结果。</p></div><span className="sf-soft-badge">无需填写 API 密钥</span></div>}

function initialize() {
  if(document.documentElement.dataset.sfUi==='mono-1')return;
  document.querySelector('#workbenchNav .aside-foot')?.remove();
  document.querySelector('#workbenchNav .brand-name small')?.remove();
  document.querySelector('#workbenchNav .brand-mark')?.remove();
  const nav=document.querySelector('#workbenchNav nav');
  if(nav){nav.replaceChildren();nav.classList.add('sf-island');createRoot(nav).render(<Navigation/>)}
  const flow=byId('sfDataFlow');if(flow)createRoot(flow).render(<DataFlow/>);
  const archive=byId('overviewArchive'),stats=byId('sfOverviewStats');if(archive&&stats)createRoot(stats).render(<OverviewStats original={archive}/>);
  const sourceHost=byId('sfSources');
  if(sourceHost){const sources=JSON.parse(byId('sfSourceData')?.textContent||'[]') as Source[];createRoot(sourceHost).render(<Sources sources={sources}/>);
    for(const input of document.querySelectorAll<HTMLInputElement>('.quick-source-grid input[name=quickSources]')){
      const source=sources.find(s=>s.id===input.value),title=input.closest('label')?.querySelector('strong');
      if(source?.logo&&title&&!title.querySelector('img')){
        const logo=document.createElement('img');logo.src=source.logo;logo.alt='';logo.width=22;logo.height=22;logo.className='sf-google-mark';
        const name=document.createElement('span');name.textContent=title.textContent;title.replaceChildren(logo,name);
      }
    }}
  const modes=document.querySelector<HTMLElement>('.quick-modes');if(modes){mount('sfQuickStart',modes,'before').render(<QuickStart/>);mount('sfRequestModes',modes,'before').render(<RequestModes original={modes}/>);modes.classList.add('sf-original-hidden')}
  const status=byId('quickStatus'),queue=byId('quickQueue'),steps=document.querySelector<HTMLElement>('.quick-steps');if(status&&queue&&steps){mount('sfRequestProgress',status,'before').render(<RequestProgress status={status} queue={queue} steps={steps}/>);status.classList.add('sf-original-hidden');queue.classList.add('sf-original-hidden');steps.classList.add('sf-original-hidden')}
  const resultRows=byId('resultRows');if(resultRows)mount('sfResultsTable',resultRows,'before').render(<ResultTable original={resultRows}/>);
  const mapsResults=byId('mapsResults');if(mapsResults)mount('sfMapsTable',mapsResults,'before').render(<MapsResults original={mapsResults}/>);
  document.documentElement.dataset.sfUi='mono-1';
  requestAnimationFrame(()=>{document.documentElement.classList.remove('sf-booting');document.getElementById('sfBootSkeleton')?.remove()});
}
initialize();
