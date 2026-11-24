"""Exercise the browser queue against a deterministic API, without collecting data."""
import subprocess
import unittest

from realtime.quick_request_ui import JS


class QuickRequestFlowTests(unittest.TestCase):
    def test_batch_sequence_failure_continuation_and_idempotent_resume(self):
        setup = r'''
const assert=require('node:assert/strict');
const elements=new Map();
function $(id){if(!elements.has(id))elements.set(id,{value:'',hidden:true,disabled:false,dataset:{},add(){},addEventListener(){}});return elements.get(id)}
$('quickRegion').value='US';$('quickLanguage').value='en';$('quickQuery').value='battery\nsolar';$('quickLimit').value='5';
const source={value:'google_web'};
const document={querySelectorAll(selector){return selector==='input[name=quickSources]:checked'?[source]:[]}};
const sessionStorage={data:{},getItem(k){return this.data[k]||null},setItem(k,v){this.data[k]=v}};
const regionNames={},productErrors={},time=x=>x||'',safeLink=x=>x,esc=x=>String(x??'');
const Option=function(){};let loadProductView=()=>{};let view='api';
let timers=[];const setTimeout=(fn)=>{timers.push(fn);return timers.length},clearTimeout=()=>{};
let calls=[],jobCounter=0,submissionFailure=false,networkFailure=false,cancelUnavailable=false;
const fetch=async(url,opt)=>{calls.push({url,body:opt.body,headers:opt.headers});let d={};
 if(url==='/api/quick-request'){
   if(networkFailure){networkFailure=false;throw Error('connection lost')}
   d={id:'job-'+(++jobCounter),token:'receipt'};
 }else if(url.endsWith('/cancel-submission')){
   if(cancelUnavailable){cancelUnavailable=false;return {ok:false,json:async()=>({error:'temporarily unconfirmed'})}}
   d={id:'original-job',token:'receipt',status:'cancelled'};
 }else if(url.endsWith('/data')){d={request_id:url.split('/')[3],source:'google_web',query:'battery',occurrences:[],topics:[]}}
 else{d={status:submissionFailure?'failed':'succeeded',error_code:'source_error'}}
 return {ok:true,json:async()=>d};
};
'''
        checks = r'''
(async()=>{
const searches=qSearches();assert.equal(searches.length,2);
quickRun={entries:[{params:searches[0],intent:'not-automatically-submitted',status:'pending'}],stopped:false};
await qRunNext(true);
assert.equal(calls.length,0);assert.equal($('quickContinue').hidden,false);
quickRun={entries:searches.map((params,i)=>({params,intent:'intent-'+i,status:'pending'})),stopped:false};
await qRunNext();assert.equal(calls.filter(x=>x.url==='/api/quick-request').length,1);assert.equal(quickRecords.length,1);
submissionFailure=true;await qRunNext();assert.equal(quickRun.entries[1].status,'failed');
await qRunNext();assert.equal(quickBusy,false);assert.equal(quickRun.entries[0].status,'succeeded');
quickRecords=[];quickRun={entries:[{params:searches[0],intent:'keep-this-key',status:'pending'}],stopped:false};networkFailure=true;
await qRunNext();assert.equal(quickRun.entries[0].id,undefined);assert.equal($('quickContinue').hidden,false);
submissionFailure=false;await qRunNext();
const retries=calls.filter(x=>x.url==='/api/quick-request'&&x.headers['Idempotency-Key']==='keep-this-key');
assert.equal(retries.length,2);assert.equal(quickRun.entries[0].status,'succeeded');
const saved=JSON.parse(sessionStorage.getItem('searchflowQuickRunV2'));assert.equal(saved.entries[0].intent,'keep-this-key');
quickRun.stopped=true;quickRun.entries.push({params:searches[1],intent:'never-submit',status:'stopped'});await qRunNext();
assert.equal(calls.some(x=>x.headers['Idempotency-Key']==='never-submit'),false);
quickRun={entries:[{params:searches[0],intent:'lost-receipt',status:'submitting'},
                   {params:searches[1],intent:'remaining',status:'pending'}],stopped:false};
const start=calls.length;
cancelUnavailable=true;
await $('quickCancel').onclick();
assert.equal(quickRun.entries[0].status,'submitting');
assert.equal(quickRun.entries[1].status,'stopped');
assert.equal($('quickCancel').hidden,false);
assert.match($('quickStatus').textContent,/尚未确认/);
quickRun=JSON.parse(sessionStorage.getItem('searchflowQuickRunV2'));
await $('quickCancel').onclick();
assert.equal(quickRun.entries[0].status,'cancelled');
assert.equal(quickRun.entries[0].id,'original-job');
assert.deepEqual(calls.slice(start).map(x=>[x.url,x.headers['Idempotency-Key']]),
  Array(2).fill(['/api/quick-request/cancel-submission','lost-receipt']));
source.value='google_trends';$('quickQuery').value='OpenAI';$('quickTrendsMode').value='keyword';$('quickTrendsTime').value='today 12-m';
$('quickWebAfter').value='2026-12-02';$('quickWebBefore').value='2026-12-01';
qPreview();assert.equal($('quickWebOptions').hidden,true);
assert.doesNotThrow(()=>qValidateLive(qSearches()));
const keyword=qSearches();assert.equal(keyword.length,1);assert.equal(keyword[0].query,'OpenAI');assert.equal(keyword[0].trends_mode,'keyword');assert.equal(keyword[0].trends_time_range,'today 12-m');
$('quickTrendsMode').value='regional_hotlist';const hotlist=qSearches();assert.equal(hotlist[0].query,'current trends');assert.equal(hotlist[0].trends_mode,'regional_hotlist');
const chart=qTrendChart({numeric_trend_series:{query:'OpenAI',region:'US',time_range:'today 3-m',points:[{timestamp:'2026-11-01',value:42,is_partial:false}]}});assert.ok(chart.includes('role="img"'));assert.ok(chart.includes('关键词热度时间序列'));assert.ok(chart.includes('42'));
console.log('queue, failure, recovery, idempotency and stop verified');
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = subprocess.run(['node'], input=setup + JS + checks, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
