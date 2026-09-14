#!/usr/bin/env python3
"""Build a local, searchable review page and CSV from the captured TOC."""
import csv
import json
from pathlib import Path
from source_scope import load_source

root = Path(__file__).parent
catalog = json.loads((root / "data/creators.json").read_text())
inventory = json.loads((root / "data/incoming-folders.json").read_text())
if catalog['creators']:
    source = load_source(root)
    if any(r.get('within_approved_group') and source.topic_id(r.get('topic_url')) is None for r in catalog['creators']):
        raise ValueError('A creator is outside the privately configured source.')
with (root / "data/creators.csv").open("w", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(["creator", "status", "categories", "topic_url", "approved_group", "ocr_confidence", "needs_review"])
    for r in catalog["creators"]:
        writer.writerow([r["name"],r.get("status", ""),", ".join(r.get("categories", [])),r["topic_url"],r["within_approved_group"],r.get("ocr_confidence", ""),r.get("needs_review", False)])
template = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram STL · Subscriptions &amp; Downloads</title><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
*{box-sizing:border-box}body{margin:0;background:#f6f5f9;color:#272332;font:15px system-ui,sans-serif}main{max-width:1440px;margin:48px auto;padding:0 24px}h1{font-size:32px;margin:8px 0}.eyebrow{color:#7651a6;font-weight:700;letter-spacing:.08em;font-size:12px}p{color:#65606e;line-height:1.6}.filters{display:flex;gap:14px;flex-wrap:wrap;margin:28px 0 18px}label{display:flex;flex-direction:column;gap:6px;font-size:12px;color:#655f70}input,select{font:inherit;font-size:15px;border:1px solid #d8d3e0;border-radius:8px;padding:11px;background:white;color:#272332}input{min-width:300px}.table{overflow:auto;background:white;border:1px solid #e3dfe9;border-radius:12px}table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:14px 18px;border-bottom:1px solid #eeeaf2;vertical-align:top}th{font-size:12px;color:#706879;background:#fbfaff}td:first-child{font-weight:600}small{display:block;color:#807787;font-weight:400;margin-top:5px}a{color:#754aaf}summary{cursor:pointer;font-size:12px;color:#706879;font-weight:400;margin-top:8px}code{display:block;font-size:11px;margin-top:8px;overflow-wrap:anywhere;max-width:300px}.badge{white-space:nowrap;font-size:12px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px}.active_covered{background:#26a85b}.active_not_covered{background:#df5b45}.on_hiatus{background:#69879b}.finished{background:#8b8493}#count{font-size:13px;color:#706879;margin:15px 0}.notice{border-left:3px solid #ae91d0;padding:8px 14px;background:#efebf6;font-size:13px}button{padding:9px 14px;border:1px solid #d8d3e0;border-radius:7px;background:white;cursor:pointer}nav{display:flex;gap:14px;align-items:center;justify-content:flex-end;margin:18px 0}
</style>
<main class="app-main">__APP_LAYOUT__
<section id="artists-view" aria-labelledby="artists-tab" hidden>
<div class="artist-controls">
<div class="filters"><label>Search creators<input id="search" type="search" placeholder="Creator name"></label><label>Subscription<select id="subscription-filter" disabled><option value="">All creators</option><option value="subscribed">Subscribed</option><option value="not_subscribed">Not subscribed</option></select></label></div>
<div class="subscription-toolbar"><div><span id="selected-count"></span><small id="saved-summary"></small></div><div class="subscription-buttons"><button id="save-subscriptions" disabled>Save subscriptions</button><button id="load-subscriptions" disabled>Load saved subscriptions</button></div></div>
<style>#save-status.save-error{color:#a12632;background:#fff0f1;border:1px solid #e4acb3;border-left:5px solid #bd283b;border-radius:8px;padding:12px 16px;font-weight:600;overflow-wrap:anywhere}</style>
<p id="save-status" aria-live="polite"></p><datalist id="incoming-folders"></datalist>
</div>
<style>
.artist-controls{background:white;border:1px solid #e3dfe9;border-radius:16px;padding:24px;margin:24px 0}
.artist-controls .filters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start;margin:0}
.artist-controls label{min-width:0;gap:8px;font-size:13px}
.artist-controls input,.artist-controls select{width:100%;min-width:0;height:44px;padding:11px 14px;font-size:14px}
.artist-controls .subscription-toolbar{margin-top:20px;padding:0;background:transparent;border-radius:0}
.subscription-buttons{display:flex;flex-wrap:wrap;gap:12px}
.artist-controls button{height:44px;padding:11px 14px;font-size:14px}
.artist-controls button:disabled,#save-subscriptions:disabled{background:#f3f1f6;color:#908896;border-color:#e3dfe9;cursor:default}
.artist-controls #save-status:empty{display:none}
@media(max-width:850px){.artist-controls{padding:20px}.artist-controls .filters{grid-template-columns:1fr}}
</style>
<div id="count" aria-live="polite"></div><div class="table"><table><thead><tr><th>Subscribe</th><th>Creator</th><th>Creator folder</th><th>Download scope</th></tr></thead><tbody id="rows"></tbody></table></div><nav><button id="previous">Previous</button><span id="page"></span><button id="next">Next</button></nav>
</section>
__QUEUE__
<style>[hidden]{display:none!important}input[type=checkbox]{min-width:0;width:18px;height:18px;accent-color:#7651a6}section{margin:40px 0}.subscription-toolbar{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap;background:#eeE8f5;border-radius:12px;padding:16px 20px}.table input[type=text]{min-width:200px;width:100%}.table select{width:100%;min-width:220px}.table input[type=month]{min-width:180px;width:100%;margin-top:8px}.table td:nth-child(2){min-width:250px}.table td:nth-child(3){min-width:270px}.table small{max-width:350px;overflow-wrap:anywhere}.table input:disabled,.table select:disabled{background:#f8f7fa;color:#908896}.changed{color:#936217}</style>
</main><script id="catalog-data" type="application/json">__DATA__</script><script id="folder-data" type="application/json">__FOLDERS__</script><script>__RULES__</script><script>
const catalog=JSON.parse(document.getElementById('catalog-data').textContent);
const inventory=JSON.parse(document.getElementById('folder-data').textContent);
for(const folder of inventory.folders)document.getElementById('incoming-folders').append(new Option(folder,folder));
const all=catalog.creators.filter(r=>r.within_approved_group).sort((a,b)=>a.name.localeCompare(b.name));
const search=document.getElementById('search');
let offset=0,filtered=[];let serverReady=false,serverRevision=0,configRevision=0,saving=false;let savedSubscriptions=[];
let selected={};try{const saved=JSON.parse(localStorage.getItem('telegram-stl-selection')||'{}');if(saved&&typeof saved==='object'&&!Array.isArray(saved))for(const [url,r] of Object.entries(saved))if(r&&typeof r==='object')selected[url]=SelectionRules.migrate(r,inventory)}catch{}
__CREATORVIEW__
__DASHBOARD__
__TABS__
</script></html>'''
def app_layout(page):
    title = {'queue': 'Download queue', 'organize': 'Organize folders', 'collages': 'Collages', 'config': 'Configuration'}[page]
    layout = (root / 'templates/app-layout.html').read_text().replace('__PAGE_TITLE__', title)
    layout = layout.replace('__QUEUE_HREF__', '#queue' if page == 'queue' else '/')
    layout = layout.replace('__ARTISTS_HREF__', '#artists' if page == 'queue' else '/#artists')
    layout = layout.replace('__TASK_NOTIFICATIONS__', (root / 'task-notifications.js').read_text())
    for name in ('queue', 'artists', 'organize', 'collages', 'config'):
        layout = layout.replace('__CURRENT_' + name.upper() + '__', 'aria-current="page"' if name == page else '')
    return layout


def write_page(name, content):
    # The running server always sees a complete page; no restart is needed.
    temporary = root / (name + '.tmp')
    temporary.write_text(content)
    temporary.replace(root / name)


pages = {
    'catalog.html': template.replace("__DATA__", json.dumps(catalog, ensure_ascii=False).replace("<", "\\u003c"))
        .replace("__FOLDERS__", json.dumps(inventory, ensure_ascii=False).replace("<", "\\u003c"))
        .replace("__RULES__", (root / "selection-rules.js").read_text())
        .replace("__QUEUE__", (root / "queue-panel.html").read_text())
        .replace("__DASHBOARD__", (root / "dashboard.js").read_text())
        .replace("__CREATORVIEW__", (root / "creator-view.js").read_text())
        .replace("__TABS__", (root / "tabs.js").read_text())
        .replace("__APP_LAYOUT__", app_layout('queue')),
    'configuration.html': (root / 'templates/configuration.html').read_text().replace('__APP_LAYOUT__', app_layout('config')),
    'organizer.html': (root / 'templates/organizer.html').read_text().replace('__APP_LAYOUT__', app_layout('organize')).replace('__RULES__', (root / 'selection-rules.js').read_text()),
    'collages.html': (root / 'templates/collages.html').read_text().replace('__APP_LAYOUT__', app_layout('collages')).replace('__COLLAGE_VIEW__', (root / 'collage-view.js').read_text()),
}
for name, content in pages.items():
    write_page(name, content)
print(f"Built four pages with shared navigation and data/creators.csv with {len(catalog['creators'])} records.")
