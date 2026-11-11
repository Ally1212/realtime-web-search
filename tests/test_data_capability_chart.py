"""Run the actual chart renderer against recorded canvas operations.

Unknowns and omitted time periods must never become fabricated continuous data.
This is a source-neutral presentation test, not a source acceptance oracle.
"""
import json
import subprocess

from realtime.data_capability_ui import JS


def test_curve_never_interpolates_unknowns_gaps_or_different_scales():
    renderer = JS[JS.index('function dcDrawCurves('):JS.index('function dcMetric(')]
    script = r'''
const assert=require('node:assert/strict');
const canvases=['one','other-scale','unplottable','large-finite'].map(id=>{const log={arcs:[],strokes:[],labels:[]};let path=[];
 const ctx={beginPath(){path=[]},moveTo(x,y){path.push([x,y])},lineTo(x,y){path.push([x,y])},stroke(){log.strokes.push(path.slice())},arc(...args){log.arcs.push(args)},fill(){},fillText(text){log.labels.push(text)}};
 return {dataset:{dcSeries:id},style:{},getContext(){return ctx},log};});
const $=()=>({querySelectorAll:()=>canvases});
function point(start,end,value,status='available'){return {period_start:start,period_end:end,metric:{value,status}};}
const series=[{id:'one',points:[
point('2026-01-01','2026-01-02','10'),point('2026-01-02','2026-01-03',null,'unknown'),
point('2026-01-03','2026-01-04','20'),point('2026-01-04','2026-01-05','0','true_zero'),
point('2026-01-08','2026-01-09','5')]},
{id:'other-scale',points:[point('2026-01-01','2026-01-02','2')]},
{id:'unplottable',points:[point('2026-01-01','2026-01-02','1e999')]},
{id:'large-finite',points:[point('2026-01-01','2026-01-02','1e308')]}];
RENDERER
 dcDrawCurves({payload:{data:{series}}});
assert.equal(canvases[0].log.arcs.length,4); // exactly four known source values
assert.equal(canvases[0].log.strokes.length,2); // axes + only the contiguous Jan3->Jan4 line
assert.equal(canvases[1].log.arcs.length,1); // separate scale, no cross-series segment
assert.equal(canvases[1].log.strokes.length,1); // axes only
assert.equal(canvases[2].log.arcs.length,0); // no infinite coordinate coercion
assert.match(canvases[2].log.labels[0],/没有可绘制/);
for(const c of canvases)for(const a of c.log.arcs)assert.ok(a.every(Number.isFinite));
'''.replace('RENDERER',renderer)
    result=subprocess.run(['node','-e',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
