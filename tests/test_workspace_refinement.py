"""Retiring workspace controls must preserve Maps, insights and data routing."""
from realtime.workspace_refinement import prepare


def test_prepare_retires_actions_without_touching_shared_data_views():
    page = '''<section id="capabilityView"><section><button>quote</button></section></section>
<section id="whaleView"><form>upload</form></section><section id="settingsView"><form>body</form></section>
<section id="mapsView">maps</section><section id="apiView">quick</section>
<script>before();
let whaleOffset=0;function loadWhale(){sendUpload()}
const refreshing=new Set();preserveRefresh();
$('settingsForm').addEventListener('input',oldSettings);
async function loadLatest(){preserveLatest()}
const p={upload_to_whale:$('taskWhaleUpload').checked};
productViews.push('capability');function dcSubmit(){sendQuote()}
(function(){const root=document.getElementById("insightsView");preserveInsights()})();
after();</script>'''
    output = prepare(page)
    assert 'sendUpload' not in output
    assert 'sendQuote' not in output
    assert 'oldSettings' not in output
    assert 'upload_to_whale:false' in output
    assert '<button>quote</button>' not in output
    assert '<form>upload</form>' not in output
    for name in ('before()', 'after()', 'preserveRefresh()', 'preserveLatest()', 'preserveInsights()',
                 '<section id="mapsView">maps</section>', '<section id="apiView">quick</section>'):
        assert name in output
    assert output.count('hidden inert aria-hidden="true"') == 3
