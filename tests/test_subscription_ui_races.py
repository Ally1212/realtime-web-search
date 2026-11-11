"""Execute real UI handlers with deliberately out-of-order responses."""
import subprocess
from realtime.subscription_ui import JS


def test_subscription_ui_ignores_stale_history_and_batch_downloads():
    harness = r'''
const assert=require('node:assert/strict');
let productKey='owner',downloads=0;
const nodes=new Map();
function $(id){if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',addEventListener(){},querySelector(){return this.ack},ack:{disabled:true},scrollIntoView(){}});return nodes.get(id)}
const esc=String,fmt=String,time=String,productEmpty=String;
const productTable=(headers,rows)=>rows.join('');
const notices=[];function notice(m){notices.push(m)}
async function loadCustomer(){}
const document={createElement(){return {click(){downloads++}}}};
const URL={createObjectURL(){return 'blob:test'},revokeObjectURL(){}};
const pending=[];
function fetch(path){return new Promise(resolve=>pending.push({path,resolve}))}
function resolveRequest(part,value){const i=pending.findIndex(x=>x.path.includes(part));assert.ok(i>=0,part);pending.splice(i,1)[0].resolve({ok:true,json:async()=>value,blob:async()=>({})})}
const tick=()=>new Promise(r=>setImmediate(r));
const batch=(id,extra={})=>({batch_id:id,sha256:id,payload:{request_count:1,event_count:2},...extra});
function button(dataset){return {dataset,disabled:false,isConnected:true,closest(){return this}}}
'''
    checks = r'''
(async()=>{
// A slower first history selection must never overwrite the second selection.
const a=showSubscriptionHistory('A'),b=showSubscriptionHistory('B');
resolveRequest('/B/status',{delivery:{}});resolveRequest('/B/batches?',{batches:[]});await b;
resolveRequest('/A/status',{delivery:{}});resolveRequest('/A/batches?',{batches:[]});await a;
assert.equal(subscriptionHistoryState.sid,'B');
// Refresh invalidates all earlier history requests, even for the same owner.
const c=showSubscriptionHistory('C');const refresh=loadSubscriptions();
resolveRequest('/C/status',{delivery:{}});resolveRequest('/C/batches?',{batches:[]});await c;
assert.equal(subscriptionHistoryState,null);
resolveRequest('/v1/data/subscriptions',{subscriptions:[]});await refresh;
// A download from an old batch may neither trigger a save nor enable the new ACK.
renderSubscriptionBatch('A',batch('old'));
const download=$('subscriptionBatch').onclick({target:button({sub:'A',download:'jsonl'})});
renderSubscriptionBatch('A',batch('new'));
$('subscriptionBatch').ack.disabled=true;
resolveRequest('/old/export',{});await download;
assert.equal(downloads,0);assert.equal($('subscriptionBatch').ack.disabled,true);
assert.equal(subscriptionBatches.get('A').downloaded,false);
// The selected batch's file unlocks only its own ACK; manifest alone does not.
let current=$('subscriptionBatch').onclick({target:button({sub:'A',download:'manifest'})});
resolveRequest('/new/export',{});await current;
assert.equal($('subscriptionBatch').ack.disabled,true);
current=$('subscriptionBatch').onclick({target:button({sub:'A',download:'csv'})});
resolveRequest('/new/export',{});await current;
assert.equal($('subscriptionBatch').ack.disabled,false);
assert.equal(subscriptionBatches.get('A').downloaded,true);
// Historical batches remain unconfirmable after downloading.
renderSubscriptionBatch('A',batch('confirmed',{acknowledged_at:'2026-11-10'}));
$('subscriptionBatch').ack.disabled=true;
current=$('subscriptionBatch').onclick({target:button({sub:'A',download:'csv'})});
resolveRequest('/confirmed/export',{});await current;
assert.equal($('subscriptionBatch').ack.disabled,true);
// An old pull cannot replace a newer displayed selection.
const pull=$('subscriptionList').onclick({target:button({sub:'A',action:'pull'})});
renderSubscriptionBatch('B',batch('selected'));
resolveRequest('/A/pull',batch('late'));await pull;
assert.equal(subscriptionBatches.get('B').batch_id,'selected');
// Switching history invalidates its in-flight detail read.
subscriptionHistoryState={sid:'A',owner:productKey};
const detail=$('subscriptionHistory').onclick({target:button({sub:'A',historyBatch:'detail'})});
subscriptionHistoryState={sid:'B',owner:productKey};
resolveRequest('/A/batches/detail',batch('detail'));await detail;
assert.equal(subscriptionBatches.get('B').batch_id,'selected');
assert.equal(pending.length,0);
console.log('7 asynchronous subscription UI scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness + JS + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
