import subprocess
import unittest

from bs4 import BeautifulSoup

from realtime.google_local_ui import JS
from realtime.maps_api_ui import JS as MAPS_WORKSPACE_JS
from realtime.serp_page import PAGE


class MapsUIFlowTests(unittest.TestCase):
    def test_create_source_opens_maps_without_adding_it_to_batch_tasks(self):
        page = BeautifulSoup(PAGE, 'html.parser')
        card = page.select_one('#taskForm .source-cards #mapsTaskSource')
        self.assertEqual(card['href'], '#maps')
        self.assertTrue(card.img['src'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(
            [source['value'] for source in page.select('#taskForm [name=taskSource]')],
            ['google_web', 'google_news', 'google_trends'],
        )
        self.assertIsNotNone(page.select_one('#quickForm input[value=google_images]'))
        self.assertEqual(len(page.select('#mapsForm')), 1)

    def test_absent_optional_developer_panel_does_not_stop_quick_request_navigation(self):
        setup=r'''
const assert=require('node:assert/strict');
const panel={name:'normal maps form'};
const elements=new Map([
 ['mapsView',{children:[panel],append(node){assert.equal(node,panel)}}],
 ['mapsForm',{}],['quickMapsWorkspace',{}],['quickForm',{}],['quickStatus',{}],
]);
const $=id=>elements.get(id)||null;
const step={};
const document={querySelectorAll(){return []},querySelector(){return step}};
const sessionStorage={getItem(){return null}};
let view='api',quickMode='live',navigations=0;
function showView(){navigations++}
'''
        checks=r'''
showView();
assert.equal(navigations,1);
assert.equal($('quickForm').hidden,false);
assert.equal($('quickMapsWorkspace').hidden,true);
elements.delete('quickMapsWorkspace');
showView();
assert.equal(navigations,2);
assert.equal($('quickForm').hidden,false);
'''
        result=subprocess.run(['node'],input=setup+MAPS_WORKSPACE_JS+checks,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_restored_preset_city_does_not_become_a_hidden_coordinate_override(self):
        setup=r'''
const assert=require('node:assert/strict');
const elements=new Map();function $(id){if(!elements.has(id))elements.set(id,{value:'',hidden:true,disabled:false,add(){},textContent:'',innerHTML:''});return elements.get(id)}
const Option=function(){};const document={createElement(){return {click(){}}}};
const localStorage={data:{'sf-maps-last':JSON.stringify({id:'one',token:'receipt'})},getItem(k){return this.data[k]||null},setItem(k,v){this.data[k]=v}};
const esc=String,fmt=String,time=String,safeLink=x=>x,notice=()=>{};
const setTimeout=()=>1,clearTimeout=()=>{};
let custom=false;
const fetch=async url=>({ok:true,json:async()=>url.endsWith('capabilities')?{enabled:true,cities:[{name:'Singapore',lat:1.29027,lng:103.851959},{name:'London',lat:51.5074,lng:-0.1278}]}:url.endsWith('/results')?{parameters:{mode:'text',query:'coffee',city:'Singapore',language:'en',radius_m:5000,location:custom?{lat:2,lng:3}:{lat:1.29027,lng:103.851959}},result_cache:{places:[]},result_expires_at:'later'}:{status:'succeeded'}});
'''
        checks=r'''
(async()=>{
await loadMaps();assert.equal($('mapsLat').value,'');assert.equal($('mapsLng').value,'');
$('mapsCity').value='London';$('mapsCity').onchange();
let p=mapsFormPayload();assert.equal(p.city,'London');assert.equal(p.location,undefined);
$('mapsLat').value='1.2';$('mapsLng').value='3.4';p=mapsFormPayload();assert.deepEqual(p.location,{lat:1.2,lng:3.4});
$('mapsCity').value='Singapore';$('mapsCity').onchange();assert.equal(mapsFormPayload().location,undefined);
custom=true;await pollMaps('one');assert.equal($('mapsLat').value,2);assert.equal($('mapsLng').value,3);
$('mapsCity').value='London';$('mapsCity').onchange();assert.equal(mapsFormPayload().location,undefined);
$('mapsMode').value='nearby';$('mapsMode').onchange();$('mapsType').value='dentist';p=mapsFormPayload();assert.equal(p.mode,'nearby');assert.equal(p.included_type,'dentist');assert.equal(p.city,'London');assert.equal($('mapsQuery').disabled,true);
console.log('city switching, preset restoration, custom coordinates and nearby mode verified');
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result=subprocess.run(['node'],input=setup+JS+checks,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
