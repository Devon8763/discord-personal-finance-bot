import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import {headers as securityHeaders} from '../local-first/serve.mjs';
const port = Number(process.argv[2] ?? 8767);
const secureHeaders=process.argv[3]==='--secure'?securityHeaders:{}; // Test-only CSP source; fixed entry has no port option.
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid local preview port');
const files = new Map([
  ['/tests/local-first-comparison.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_comparison_ui_browser.mjs', ['local_first_comparison_ui_browser.mjs', 'text/javascript']],
  ['/local-first/comparison.mjs', ['../local-first/comparison.mjs', 'text/javascript']],
  ['/local-first/vendor/chartjs-4.5.1/chart.umd.min.js', ['../web/static/vendor/chartjs-4.5.1/chart.umd.min.js', 'text/javascript']],
  ['/tests/local_first_expense_browser.html', ['local_first_expense_browser.html', 'text/html']],
  ['/tests/local_first_expense_browser.mjs', ['local_first_expense_browser.mjs', 'text/javascript']],
  ['/tests/local-first-recurring.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_recurring_ui_browser.mjs', ['local_first_recurring_ui_browser.mjs', 'text/javascript']],
  ['/tests/local_first_recurring_browser.html', ['local_first_recurring_browser.html', 'text/html']],
  ['/tests/local_first_recurring_browser.mjs', ['local_first_recurring_browser.mjs', 'text/javascript']],
  ['/tests/local-first-csv.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_csv_ui_browser.mjs', ['local_first_csv_ui_browser.mjs', 'text/javascript']],
  ['/local-first/csv.mjs', ['../local-first/csv.mjs', 'text/javascript']],
  ['/tests/local-first-persistence.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_persistence_browser.mjs', ['local_first_persistence_browser.mjs', 'text/javascript']],
  ['/tests/local-first-crypto.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_crypto_ui_browser.mjs', ['local_first_crypto_ui_browser.mjs', 'text/javascript']],
  ['/tests/local_first_origin_browser.html', ['local_first_origin_browser.html', 'text/html']],
  ['/tests/local_first_origin_browser.mjs', ['local_first_origin_browser.mjs', 'text/javascript']],
  ['/tests/local-first-fixed.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_fixed_ui_browser.mjs', ['local_first_fixed_ui_browser.mjs', 'text/javascript']],
  ['/tests/local_first_fixed_browser.html', ['local_first_fixed_browser.html', 'text/html']],
  ['/tests/local_first_fixed_browser.mjs', ['local_first_fixed_browser.mjs', 'text/javascript']],
  ['/local-first/', ['../local-first/index.html', 'text/html']],
  ...['index.html', 'style.css', 'rules.mjs', 'browse.mjs', 'ledger.mjs', 'idb.mjs', 'page.mjs', 'backup.mjs', 'backup-crypto.mjs'].map(name => ['/local-first/' + name, ['../local-first/' + name, name.endsWith('.css') ? 'text/css' : name.endsWith('.html') ? 'text/html' : 'text/javascript']]),
  ['/local-first/sw.js', ['../local-first/sw.js', 'text/javascript']],
  ...['lossless-json.js', 'lossless-json.js.map'].map(name => ['/local-first/vendor/lossless-json-4.3.1/' + name, ['../local-first/vendor/lossless-json-4.3.1/' + name, name.endsWith('.map') ? 'application/json' : 'text/javascript']]),
  ['/tests/local_first_portable_browser.html', ['local_first_portable_browser.html', 'text/html']],
  ['/tests/local_first_portable_browser.mjs', ['local_first_portable_browser.mjs', 'text/javascript']],
  ['/tests/local_first_browser.html', ['local_first_browser.html', 'text/html']],
  ['/tests/local_first_browser.mjs', ['local_first_browser.mjs', 'text/javascript']],
  ['/tests/local_first_storage_browser.html', ['local_first_storage_browser.html', 'text/html']],
  ['/tests/local_first_storage_browser.mjs', ['local_first_storage_browser.mjs', 'text/javascript']],
  ['/tests/local_first_settings_browser.html', ['local_first_settings_browser.html', 'text/html']],
  ['/tests/local_first_settings_browser.mjs', ['local_first_settings_browser.mjs', 'text/javascript']],
  ['/tests/local_first_budgets_browser.html', ['local_first_budgets_browser.html', 'text/html']],
  ['/tests/local_first_budgets_browser.mjs', ['local_first_budgets_browser.mjs', 'text/javascript']],
  ['/tests/local_first_budgets_ui_browser.mjs', ['local_first_budgets_ui_browser.mjs', 'text/javascript']],
  ['/tests/local-first-budgets.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_offline_settings_browser.html', ['local_first_offline_settings_browser.html', 'text/html']],
  ['/tests/local_first_offline_settings_browser.mjs', ['local_first_offline_settings_browser.mjs', 'text/javascript']],
  ['/tests/local-first-settings.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local_first_settings_ui_browser.mjs', ['local_first_settings_ui_browser.mjs', 'text/javascript']],
  ['/tests/local_first_scale_browser.html', ['local_first_scale_browser.html', 'text/html']],
  ['/tests/local_first_scale_browser.mjs', ['local_first_scale_browser.mjs', 'text/javascript']],
  ['/tests/local_first_ui_browser.mjs', ['local_first_ui_browser.mjs', 'text/javascript']],
  ['/tests/local_first_browse_browser.mjs', ['local_first_browse_browser.mjs', 'text/javascript']],
  ['/tests/local_first_entry_browser.mjs', ['local_first_entry_browser.mjs', 'text/javascript']],
  ['/tests/local-first-ui.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local-first-browse.html', ['../local-first/index.html', 'text/html']],
  ['/tests/local-first-entry.html', ['../local-first/index.html', 'text/html']],
  ['/tests/fixtures/life_ledger_rules.json', ['fixtures/life_ledger_rules.json', 'application/json']],
  ['/tests/fixtures/portable_life_ledger.json', ['fixtures/portable_life_ledger.json', 'application/json']],
]);
// File-only loopback preview: no ledger API, directory listing, or access to other project files.
createServer(async (request, response) => {
  const path = new URL(request.url, 'http://127.0.0.1').pathname;
  console.log(request.method, path);
  if (request.method === 'GET' && path === '/tests/local-first-narrow.html') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    response.end('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>375px 合成頁面布局驗證</title><h1>375px 瀏覽器子頁面驗證</h1><iframe title="本機記帳驗證" src="/local-first/" width="375" height="1100" style="border:0"></iframe></html>');
    return;
  }
  const file = files.get(path);
  if(process.argv[4]==='--no-charts'&&path==='/local-first/vendor/chartjs-4.5.1/chart.umd.min.js'){response.writeHead(503,secureHeaders);response.end();return;}
  if (request.method !== 'GET' || !file) { response.writeHead(404); response.end(); return; }
  try {
    let body = await readFile(new URL(file[0], import.meta.url));
    // Isolated update test only: install the preceding 12-asset worker, then restart without this flag.
    if(process.argv[4]==='--previous-worker'&&path==='/local-first/sw.js')body=body.toString('utf8').replace('0.13.12-v2','0.13.11-v1')
      .replace("  '/local-first/comparison.mjs',\n",'').replace("  '/local-first/vendor/chartjs-4.5.1/chart.umd.min.js',\n",'');
    if(path==='/tests/local-first-comparison.html')body=body.toString('utf8').replace('href="style.css"','href="/local-first/style.css"').replace('src="page.mjs"','src="/tests/local_first_comparison_ui_browser.mjs"');
    if (path === '/tests/local-first-recurring.html') {
      body=body.toString('utf8').replace('href="style.css"','href="/local-first/style.css"')
        .replace('src="page.mjs"','src="/tests/local_first_recurring_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-csv.html') {
      body=body.toString('utf8').replace('href="style.css"','href="/local-first/style.css"')
        .replace('src="page.mjs"','src="/tests/local_first_csv_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-persistence.html') {
      body=body.toString('utf8').replace('href="style.css"','href="/local-first/style.css"')
        .replace('src="page.mjs"','src="/tests/local_first_persistence_browser.mjs"');
    }
    if (path === '/tests/local-first-crypto.html') {
      body=body.toString('utf8').replace('href="style.css"','href="/local-first/style.css"')
        .replace('src="page.mjs"','src="/tests/local_first_crypto_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-fixed.html') {
      body = body.toString('utf8').replace('<head>', '<head><base href="/local-first/">')
        .replace('src="page.mjs"', 'src="/tests/local_first_fixed_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-ui.html') {
      body = body.toString('utf8').replace('href="style.css"', 'href="/local-first/style.css"')
        .replace('src="page.mjs"', 'src="/tests/local_first_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-settings.html') {
      body = body.toString('utf8').replace('<head>', '<head><base href="/local-first/">')
        .replace('src="page.mjs"', 'src="/tests/local_first_settings_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-budgets.html') {
      body = body.toString('utf8').replace('<head>', '<head><base href="/local-first/">')
        .replace('src="page.mjs"', 'src="/tests/local_first_budgets_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-browse.html') {
      body = body.toString('utf8').replace('href="style.css"', 'href="/local-first/style.css"')
        .replace('src="page.mjs"', 'src="/tests/local_first_browse_browser.mjs"');
    }
    if (path === '/tests/local-first-entry.html') {
      body = body.toString('utf8').replace('<head>', '<head><base href="/local-first/">')
        .replace('src="page.mjs"', 'src="/tests/local_first_entry_browser.mjs"');
    }
    response.writeHead(200, { ...secureHeaders, 'Content-Type': file[1] + '; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    response.end(body);
  } catch { response.writeHead(404); response.end(); }
}).listen(port, '127.0.0.1', () => console.log(`Synthetic static preview: http://127.0.0.1:${port}/local-first/`));
