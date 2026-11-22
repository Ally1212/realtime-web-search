"""Only public presentation fields may cross the Maps React bridge."""
import subprocess
from realtime.component_ui import BRIDGE


def test_maps_bridge_preserves_renderer_and_clears_stale_results_on_new_query():
    script = r'''
const assert = require('node:assert/strict');
let submit, notifications=0, renders=0;
const window={dispatchEvent(){notifications++}};
const document={getElementById(){return {addEventListener(name,fn,capture){assert.equal(name,'submit');assert.equal(capture,true);submit=fn}}}};
const Event=function(){};
let renderMapPlaces=rows=>{renders++;return '<article>'+rows[0].name+'</article>'};
'''+BRIDGE+r'''
const html=renderMapPlaces([{name:'Saved shop',rating:0,website_uri:'https://example.com',token:'private-receipt',raw_source:{secret:'not-for-ui'}}]);
assert.equal(html,'<article>Saved shop</article>');assert.equal(renders,1);
assert.equal(window.sfComponentBridge.maps[0].rating,0);
assert.equal(window.sfComponentBridge.maps[0].national_phone_number,null);
assert.equal('token' in window.sfComponentBridge.maps[0],false);
assert.equal('raw_source' in window.sfComponentBridge.maps[0],false);
submit();assert.deepEqual(window.sfComponentBridge.maps,[]);assert.equal(notifications,2);
'''
    result=subprocess.run(['node'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
