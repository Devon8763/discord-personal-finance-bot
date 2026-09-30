import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../local-first/sw.js', import.meta.url), 'utf8');
const required = ['/local-first/', '/local-first/index.html', '/local-first/style.css', '/local-first/page.mjs', '/local-first/ledger.mjs', '/local-first/rules.mjs', '/local-first/backup.mjs', '/local-first/vendor/lossless-json-4.3.1/lossless-json.js'];

function worker({ broken = '', existing = new Map() } = {}) {
  const handlers = new Map();
  const storage = new Map(existing);
  const network = [];
  const caches = {
    open: async name => {
      if (!storage.has(name)) storage.set(name, new Map());
      const entries = storage.get(name);
      return {
        addAll: async paths => {
          for (const path of paths) {
            network.push(path);
            if (path === broken) throw new Error('missing asset');
          }
          for (const path of paths) entries.set(path, { path });
        },
        match: async path => entries.get(path),
        keys: async () => [...entries.keys()].map(url => ({ url: 'http://127.0.0.1:8767' + url })),
      };
    },
    keys: async () => [...storage.keys()],
    delete: async name => storage.delete(name),
  };
  const self = {
    location: { origin: 'http://127.0.0.1:8767' },
    registration: { scope: 'http://127.0.0.1:8767/local-first/' },
    clients: { claim: async () => {} },
    addEventListener: (kind, callback) => handlers.set(kind, callback),
  };
  vm.runInNewContext(source, { self, caches, URL, Promise, Response: { error: () => ({ error: 'cache-miss' }) } });
  const run = async kind => {
    let pending;
    handlers.get(kind)({ waitUntil: task => { pending = task; } });
    await pending;
  };
  const fetch = async (path, method = 'GET') => {
    let response;
    handlers.get('fetch')({ request: { url: path.startsWith('http:') ? path : 'http://127.0.0.1:8767' + path, method }, respondWith: task => { response = task; } });
    return response && await response;
  };
  const ready = async () => {
    let answer;
    handlers.get('message')({ data: { type: 'offline-ready' }, ports: [{ postMessage: value => { answer = value; } }], waitUntil: task => { answer = task.then(() => answer); } });
    return await answer;
  };
  return { run, fetch, ready, storage, network };
}

test('complete install caches only required local-first assets and serves them offline', async () => {
  const sw = worker();
  await sw.run('install');
  assert.deepEqual(sw.network, required);
  assert.equal(await sw.ready(), true);
  assert.deepEqual(await sw.fetch('/local-first/'), { path: '/local-first/' });
  for (const path of ['/tests/fixture.json', '/local-first/sw.js', '/web/', '/local-first/other.json', '/local-first/life-ledger-backup-v1.json', '/local-first/?owner=1', 'http://other.example/local-first/']) assert.equal(await sw.fetch(path), undefined);
  assert.equal(await sw.fetch('/local-first/', 'POST'), undefined);
});

test('missing asset prevents readiness and failed update leaves old cache', async () => {
  const old = new Map([['local-first-offline-previous', new Map([['/local-first/', { path: '/local-first/' }]])]]);
  const sw = worker({ broken: '/local-first/backup.mjs', existing: old });
  await assert.rejects(sw.run('install'), /missing asset/);
  assert.equal(await sw.ready(), false);
  assert.ok(sw.storage.has('local-first-offline-previous'));
  assert.deepEqual([...sw.storage.get('local-first-offline-previous').keys()], ['/local-first/']);
});

test('activation removes only this feature’s old cache after successful install', async () => {
  const sw = worker({ existing: new Map([['local-first-offline-previous', new Map()], ['other-cache', new Map()]]) });
  await sw.run('install');
  await sw.run('activate');
  assert.equal(sw.storage.has('local-first-offline-previous'), false);
  assert.equal(sw.storage.has('other-cache'), true);
  assert.equal(await sw.ready(), true);
  sw.storage.get('local-first-offline-0.12.2-v1').delete('/local-first/backup.mjs');
  assert.equal(await sw.ready(), false);
  assert.deepEqual(await sw.fetch('/local-first/backup.mjs'), { error: 'cache-miss' });
});

test('incomplete new cache cannot activate or remove the old version', async () => {
  const sw = worker({ existing: new Map([['local-first-offline-previous', new Map()]]) });
  await sw.run('install');
  sw.storage.get('local-first-offline-0.12.2-v1').delete('/local-first/backup.mjs');
  await assert.rejects(sw.run('activate'), /Incomplete offline cache/);
  assert.equal(sw.storage.has('local-first-offline-previous'), true);
});
