import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

export const HOST = '127.0.0.1';
export const PORT = 8768;
export const ENTRY_URL = `http://${HOST}:${PORT}/local-first/`;
const files = new Map([
  ['/local-first/', ['index.html', 'text/html']],
  ['/local-first/vendor/chartjs-4.5.1/chart.umd.min.js', ['../web/static/vendor/chartjs-4.5.1/chart.umd.min.js', 'text/javascript']],
  ...['index.html', 'style.css', 'page.mjs', 'rules.mjs', 'browse.mjs', 'csv.mjs', 'comparison.mjs', 'ledger.mjs', 'idb.mjs', 'backup.mjs', 'backup-crypto.mjs', 'sw.js',
    'vendor/lossless-json-4.3.1/lossless-json.js'].map(name => ['/local-first/' + name,
    [name, name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript']]),
]);
export const headers = {
  'Cache-Control':'no-store',
  'X-Content-Type-Options':'nosniff',
  'Referrer-Policy':'no-referrer',
  'X-Frame-Options':'DENY',
  'Cross-Origin-Resource-Policy':'same-origin',
  'Cross-Origin-Opener-Policy':'same-origin',
  'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
  'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; worker-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'none'",
};

export function createLocalServer() {
  const server = createServer(async (request, response) => {
    const refuse = (status, extra = {}) => {
      response.writeHead(status, {...headers, 'Content-Type':'text/plain; charset=utf-8', 'Connection':'close', ...extra});
      response.end('此請求無法提供。');
    };
    const hosts = request.rawHeaders.filter((value, index) => index % 2 === 0 && value.toLowerCase() === 'host');
    if (hosts.length !== 1 || request.headers.host !== `${HOST}:${PORT}`) { refuse(403); return; }
    if (request.method !== 'GET') { refuse(405, {Allow:'GET'}); return; }
    if (request.headers['transfer-encoding'] !== undefined ||
        (request.headers['content-length'] !== undefined && request.headers['content-length'] !== '0')) { refuse(400); return; }
    // Exact raw targets only: never normalize paths, accept queries, or join caller input to disk paths.
    const file = files.get(request.url);
    if (!file) { refuse(404); return; }
    try {
      const body = await readFile(new URL(file[0], import.meta.url));
      response.writeHead(200, {...headers, 'Content-Type':file[1] + '; charset=utf-8'});
      response.end(body);
    } catch { refuse(503); }
  });
  server.on('clientError', (_error, socket) => {
    if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n' +
      Object.entries(headers).map(([key, value]) => `${key}: ${value}\r\n`).join('') + '\r\n');
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 2) {
    console.error('啟動參數無效；固定本機入口不接受更換連接埠。');
    process.exitCode = 1;
  } else {
    const server = createLocalServer();
    server.on('error', error => {
      console.error(error.code === 'EADDRINUSE'
        ? '啟動失敗：固定連接埠 8768 已被占用。請確認占用程式；本入口不會更換連接埠。'
        : '本機入口啟動失敗。請檢查本機執行環境後重試。');
      process.exitCode = 1;
    });
    server.listen(PORT, HOST, () => {
      console.log('固定本機入口；僅供合成資料測試，請勿輸入真實帳目。');
      console.log('備份未加密，Python Web 尚未被取代。');
      console.log(ENTRY_URL);
      console.log('請保持此視窗開啟；按 Ctrl+C 停止服務。');
    });
  }
}
