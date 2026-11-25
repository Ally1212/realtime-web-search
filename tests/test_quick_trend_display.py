import subprocess

from realtime.quick_request_ui import JS


def test_trend_chart_keeps_unobserved_points_distinct_from_zero():
    before, _, chart_and_rest = JS.partition('function qTrendChart(topic){')
    assert before and chart_and_rest
    chart = 'function qTrendChart(topic){' + chart_and_rest.split('\nfunction qRender', 1)[0]
    scenario = r"""
    const assert = require('node:assert/strict');
    const esc = String;
    const series = { query:'Example', region:'US', points:[
      { timestamp:'day 1', value:0, has_data:true },
      { timestamp:'day 2', value:5, has_data:true },
      { timestamp:'day 3', value:0, has_data:false },
      { timestamp:'day 4', value:7, has_data:true },
      { timestamp:'day 5', value:8, has_data:true }
    ]};
    const output = qTrendChart({numeric_trend_series: series});
    assert.equal((output.match(/<td>0<\/td>/g) || []).length, 1);
    assert.equal((output.match(/<td>无数据<\/td>/g) || []).length, 1);
    assert.equal((output.match(/<polyline /g) || []).length, 2);
    """
    result = subprocess.run(['node'], input=chart + scenario, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
