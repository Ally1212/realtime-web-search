"""Exercise real refresh logic while the historical summary request is unresolved."""
from pathlib import Path
import subprocess


def test_home_stays_visible_while_summary_is_loading():
    source = Path('realtime/assets/workspace-refinement.js').read_text()
    code = source[source.index('const viewRead=new Map();'):source.index('// One task drawer;')]
    harness = r'''
const assert=require('node:assert/strict');
const classes=new Set(),attrs={};
const section={classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)},setAttribute:(k,v)=>attrs[k]=v};
const $=id=>id.endsWith('View')?section:{textContent:'',setAttribute(){},innerHTML:''};
const removed=[],productViews=[],refreshing=new Set();let refreshView,view='overview';
let releaseSummary,latestFinished=false;
const loadLatest=async()=>{latestFinished=true;};
const loadArchiveOverview=()=>new Promise(resolve=>releaseSummary=resolve);
const loadTasks=()=>new Promise(()=>{});
const notice=()=>{};
CODE
(async()=>{
 const pending=refreshView();
 await Promise.resolve();
 assert.equal(latestFinished,true);
 assert.equal(classes.has('sf-view-loading'),false,'slow summary must not hide the home content');
 assert.equal(attrs['aria-busy'],'true');
 releaseSummary();await pending;
 assert.equal(attrs['aria-busy'],'false');
 view='tasks';refreshView();
 assert.equal(classes.has('sf-view-loading'),true,'other views retain their initial loading state');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('CODE', code)
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
