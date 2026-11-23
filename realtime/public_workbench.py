"""Public SearchFlow entry with the existing quick search and Maps APIs."""
from html import escape
from pathlib import Path

from .maps_provider import CITIES, availability
from .product import middle_images_enabled
from .ui_icons import source_icon

ASSETS = Path(__file__).with_name('assets')
FILES = {
    '/app.js': ('public-workbench.js', 'text/javascript; charset=utf-8'),
    '/app.css': ('public-workbench.css', 'text/css; charset=utf-8'),
}
REGIONS = {'Singapore': 'SG', 'London': 'GB', 'New York': 'US', 'Tokyo': 'JP',
           'Hong Kong': 'HK', 'Taipei': 'TW', 'Sydney': 'AU', 'Berlin': 'DE'}
PAGE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SearchFlow</title>
<link rel="stylesheet" href="/app.css">
<script src="/app.js" defer></script>
</head>
<body>
<header>
  <a href="/app" class="brand">SearchFlow</a>
  <button id="language" type="button" class="quiet" aria-label="中文, switch to Simplified Chinese"><span lang="zh-CN">中文</span></button>
  <form method="post" action="/api/account/logout" id="logout" hidden><button class="quiet" data-text="logout">Sign out</button></form>
</header>
<main>
  <h1 data-text="heading">Search and export data</h1>
  <p data-text="intro">Run a search, view your results, and download them as CSV or JSONL.</p>
  <p role="status" id="notice"></p>
  <section id="welcome">
    <a class="action google" href="/api/account/google/start">GOOGLE_MARK<span data-text="signin">Sign in with Google</span></a>
    <p data-text="limits">Free access includes 5 new requests per UTC day and 30 successful searches per UTC month.</p>
  </section>
  <section id="workspace" hidden>
    <p id="usage" role="status"></p>
    <form id="search">
      <div class="fields">
        <label><span data-text="source">Source</span>
          <select id="source">
            <option value="google_web" translate="no">Google Search</option>
            MAPS_OPTION
            <option value="google_news" translate="no">Google News</option>
            IMAGES_OPTION
            <option value="google_trends" translate="no">Google Trends</option>
          </select>
        </label>
        <label><span data-text="language">Result language</span>
          <select id="resultLanguage">
            <option value="en" data-text="english">English</option>
            <option value="zh" data-text="chinese">Simplified Chinese</option>
          </select>
        </label>
      </div>
      <div class="fields" id="mapFields" hidden>
        <label><span data-text="mode">Search type</span>
          <select id="mapMode">
            <option value="text" data-text="keyword">Keyword</option>
            <option value="nearby" data-text="nearby">Nearby businesses</option>
          </select>
        </label>
        <label><span data-text="city">City</span><select id="city">CITIES</select></label>
        <label><span data-text="radius">Radius</span>
          <select id="radius"><option value="1000">1 km</option><option value="5000" selected>5 km</option><option value="10000">10 km</option></select>
        </label>
      </div>
      <label><span id="queryLabel">Keywords</span><input id="query" name="q" maxlength="300" required></label>
      <button id="submit" class="action" data-text="search">Search</button>
    </form>
    <section id="output" aria-labelledby="resultTitle" hidden>
      <div class="result-header">
        <h2 id="resultTitle" data-text="results">Results</h2>
        <div id="downloads" hidden>
          <a id="csv" data-text="csv">Download CSV</a>
          <a id="jsonl" data-text="jsonl">Download JSONL</a>
        </div>
      </div>
      <ul id="rows"></ul>
      <details id="raw" hidden><summary data-text="json">View JSON</summary><pre id="payload"></pre></details>
    </section>
  </section>
</main>
<footer><a href="PRIVACY" data-text="privacy">Privacy</a><a href="TERMS" data-text="terms">Terms</a></footer>
</body>
</html>'''


def page(config):
    cities = ''.join(f'<option value="{escape(city)}" data-region="{REGIONS.get(city, "US")}">{escape(city)}</option>'
                     for city in CITIES)
    return (PAGE.replace('>CITIES<', '>' + cities + '<')
            .replace('GOOGLE_MARK', source_icon('google_web', 18))
            .replace('MAPS_OPTION', '<option value="google_maps" translate="no">Google Maps</option>'
                     if availability()['enabled'] else '')
            .replace('IMAGES_OPTION', '<option value="google_images" translate="no">Google Images</option>'
                     if middle_images_enabled() else '')
            .replace('href="PRIVACY"', f'href="{escape(config.privacy_url)}"')
            .replace('href="TERMS"', f'href="{escape(config.terms_url)}"'))


def asset(path):
    filename, mime = FILES[path]
    return (ASSETS / filename).read_bytes(), mime
