import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
const port = Number(process.argv[2] ?? 8767);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid local preview port');
const files = new Map([
  ['/local-first/', ['../local-first/index.html', 'text/html']],
  ...['index.html', 'style.css', 'rules.mjs', 'ledger.mjs', 'idb.mjs', 'page.mjs', 'backup.mjs'].map(name => ['/local-first/' + name, ['../local-first/' + name, name.endsWith('.css') ? 'text/css' : name.endsWith('.html') ? 'text/html' : 'text/javascript']]),
  ['/local-first/sw.js', ['../local-first/sw.js', 'text/javascript']],
  ...['lossless-json.js', 'lossless-json.js.map'].map(name => ['/local-first/vendor/lossless-json-4.3.1/' + name, ['../local-first/vendor/lossless-json-4.3.1/' + name, name.endsWith('.map') ? 'application/json' : 'text/javascript']]),
  ['/tests/local_first_portable_browser.html', ['local_first_portable_browser.html', 'text/html']],
  ['/tests/local_first_portable_browser.mjs', ['local_first_portable_browser.mjs', 'text/javascript']],
  ['/tests/local_first_browser.html', ['local_first_browser.html', 'text/html']],
  ['/tests/local_first_browser.mjs', ['local_first_browser.mjs', 'text/javascript']],
  ['/tests/local_first_storage_browser.html', ['local_first_storage_browser.html', 'text/html']],
  ['/tests/local_first_storage_browser.mjs', ['local_first_storage_browser.mjs', 'text/javascript']],
  ['/tests/local_first_scale_browser.html', ['local_first_scale_browser.html', 'text/html']],
  ['/tests/local_first_scale_browser.mjs', ['local_first_scale_browser.mjs', 'text/javascript']],
  ['/tests/local_first_ui_browser.mjs', ['local_first_ui_browser.mjs', 'text/javascript']],
  ['/tests/local_first_entry_browser.mjs', ['local_first_entry_browser.mjs', 'text/javascript']],
  ['/tests/local-first-ui.html', ['../local-first/index.html', 'text/html']],
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
  if (request.method !== 'GET' || !file) { response.writeHead(404); response.end(); return; }
  try {
    let body = await readFile(new URL(file[0], import.meta.url));
    if (path === '/tests/local-first-ui.html') {
      body = body.toString('utf8').replace('<head>', '<head><base href="/local-first/">')
        .replace('src="page.mjs"', 'src="/tests/local_first_ui_browser.mjs"');
    }
    if (path === '/tests/local-first-entry.html') {
      body = body.toString('utf8').replace('<head>', '<head><base href="/local-first/">')
        .replace('src="page.mjs"', 'src="/tests/local_first_entry_browser.mjs"');
    }
    response.writeHead(200, { 'Content-Type': file[1] + '; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    response.end(body);
  } catch { response.writeHead(404); response.end(); }
}).listen(port, '127.0.0.1', () => console.log(`Synthetic static preview: http://127.0.0.1:${port}/local-first/`));
