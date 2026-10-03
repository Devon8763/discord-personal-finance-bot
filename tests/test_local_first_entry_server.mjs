import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { request } from 'node:http';
import { createConnection } from 'node:net';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const script=new URL('../local-first/serve.mjs',import.meta.url);
const load=async()=>{assert.notEqual(await readFile(script).catch(()=>null),null,'Missing fixed local entry server');return import(script.href);};
const start=server=>new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',()=>resolve(server.address().port));});
const stop=server=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
const get=(port,path='/local-first/',headers={},method='GET')=>new Promise((resolve,reject)=>{
  const req=request({hostname:'127.0.0.1',port,path,method,headers:{Host:'127.0.0.1:8768',...headers}},res=>{
    const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));
  });req.on('error',reject);req.end();
});
const withServer=async fn=>{const {createLocalServer}=await load(),server=createLocalServer();const port=await start(server);try{await fn(port);}finally{await stop(server);}};
const raw=(port,text)=>new Promise((resolve,reject)=>{const socket=createConnection(port,'127.0.0.1');let reply='';socket.setTimeout(3000,()=>socket.destroy(new Error('Response timeout')));socket.on('connect',()=>socket.end(text));socket.on('data',data=>{reply+=data;});socket.on('end',()=>resolve(reply));socket.on('error',reject);});
const security=headers=>{
  assert.equal(headers['x-content-type-options'],'nosniff');assert.equal(headers['referrer-policy'],'no-referrer');
  assert.equal(headers['x-frame-options'],'DENY');assert.equal(headers['cross-origin-resource-policy'],'same-origin');
  assert.equal(headers['cross-origin-opener-policy'],'same-origin');assert.equal(headers['cache-control'],'no-store');
  const csp=headers['content-security-policy'];assert.ok(csp,'Missing CSP');
  for(const value of ["default-src 'none'","script-src 'self'","style-src 'self'","connect-src 'self'","worker-src 'self'","base-uri 'none'","object-src 'none'","frame-ancestors 'none'","form-action 'none'"]) assert.ok(csp.includes(value),value);
  assert.ok(!/unsafe-inline|unsafe-eval|\*|https?:|data:/.test(csp),'CSP loosened');
  assert.equal(headers['access-control-allow-origin'],undefined);
};
test('comparison module and reused Chart.js are local, byte-identical and retain MIT provenance',async()=>withServer(async port=>{
  const {createHash}=await import('node:crypto');
  const directory=new URL('../web/static/vendor/chartjs-4.5.1/',import.meta.url);
  const provenance=JSON.parse(await readFile(new URL('provenance.json',directory),'utf8'));
  for(const [name,hash] of Object.entries(provenance.sha256)){
    assert.equal(createHash('sha256').update(await readFile(new URL(name,directory))).digest('hex'),hash,name);
  }
  const response=await get(port,'/local-first/vendor/chartjs-4.5.1/chart.umd.min.js');
  assert.equal(response.status,200);security(response.headers);assert.deepEqual(response.body,await readFile(new URL('chart.umd.min.js',directory)));
  assert.equal((await get(port,'/local-first/comparison.mjs')).status,200);
  for(const path of ['/web/static/vendor/chartjs-4.5.1/','/local-first/vendor/chartjs-4.5.1/provenance.json','/local-first/vendor/chartjs-4.5.1/chart.umd.min.js.map'])assert.equal((await get(port,path)).status,404);
}));

test('fixed entry constants and Windows launcher preserve failures without changing ports',async()=>{
  const {HOST,PORT,ENTRY_URL}=await load();assert.equal(HOST,'127.0.0.1');assert.equal(PORT,8768);assert.equal(ENTRY_URL,'http://127.0.0.1:8768/local-first/');
  const bytes=await readFile(new URL('../start_local_first.local.cmd',import.meta.url)).catch(()=>null);
  assert.ok(bytes,'Missing Windows launcher');const text=bytes.toString('utf8');
  assert.ok(text.includes('local-first\\serve.mjs'));assert.ok(text.includes('pause'));
  assert.ok(!/uvicorn|DISCORDBOT_DB_PATH|data\.db|\.env|--port|%\*/.test(text),'Launcher crosses boundary');
  assert.ok(!/(?<!\r)\n/.test(text),'CMD must use CRLF');
});
test('required local assets are byte-identical, correctly typed and covered by CSP',async()=>withServer(async port=>{
  const paths=['index.html','style.css','page.mjs','rules.mjs','browse.mjs','csv.mjs','ledger.mjs','idb.mjs','backup.mjs','backup-crypto.mjs','sw.js','vendor/lossless-json-4.3.1/lossless-json.js'];
  for(const name of paths){const response=await get(port,'/local-first/'+name);assert.equal(response.status,200,name);security(response.headers);
    assert.deepEqual(response.body,await readFile(new URL('../local-first/'+name,import.meta.url)),name);
    assert.match(response.headers['content-type'],name.endsWith('.css')?/^text\/css/:name.endsWith('.html')?/^text\/html/:/^text\/javascript/);
  }
  assert.deepEqual((await get(port)).body,await readFile(new URL('../local-first/index.html',import.meta.url)));
  assert.match((await get(port)).body.toString(),/僅供合成資料測試，請勿輸入真實帳目/);
}));
test('invalid Host, duplicates, missing authority and absolute request targets are refused',async()=>withServer(async port=>{
  for(const host of ['localhost:8768','127.0.0.1','127.0.0.1:8767','evil.example:8768','[::1]:8768']){
    const response=await get(port,'/local-first/',{Host:host});assert.equal(response.status,403);security(response.headers);assert.ok(!response.body.toString().includes(host));
  }
  for(const text of ['GET /local-first/ HTTP/1.1\r\n\r\n','GET /local-first/ HTTP/1.1\r\nHost: 127.0.0.1:8768\r\nHost: evil.example\r\n\r\n']) assert.match(await raw(port,text),/^HTTP\/1\.1 (400|403)/);
  assert.equal((await get(port,'http://evil.example/local-first/')).status,404);
}));
test('only GET is served; no upload, GET body or reflected method/error details',async()=>withServer(async port=>{
  for(const method of ['HEAD','POST','PUT','PATCH','DELETE','OPTIONS','TRACE']){const response=await get(port,'/local-first/',{},method);assert.equal(response.status,405);assert.equal(response.headers.allow,'GET');security(response.headers);}
  const reply=await raw(port,'GET /local-first/ HTTP/1.1\r\nHost: 127.0.0.1:8768\r\nContent-Length: 6\r\n\r\nSECRET');
  assert.match(reply,/^HTTP\/1\.1 400/);assert.ok(!reply.includes('SECRET'));
}));
test('sensitive, unlisted, encoded, traversal and query paths never reach filesystem or directory listings',async()=>withServer(async port=>{
  for(const path of ['/', '/local-first','/.env','/Token','/data.db','/life-ledger-backup-v1.json','/.git/config',
    '/tests/local_first_browser.html','/tests/fixtures/portable_life_ledger.json','/AGENTS.md','/README.md',
    '/local-first/serve.mjs','/local-first/vendor/','/local-first/vendor/lossless-json-4.3.1/lossless-json.js.map',
    '/local-first/../.env','/local-first/../local-first/index.html','/local-first/%2e%2e/.env','/%2flocal-first/index.html',
    '/local-first/index.html?secret=PRIVATE-PROBE','//local-first/index.html','/local-first\\index.html']){
    const response=await get(port,path);assert.equal(response.status,404,path);security(response.headers);
    assert.ok(!/PRIVATE-PROBE|\.env|data\.db|Index of|Directory listing|C:\\|Error:/.test(response.body.toString()),path);
  }
}));
test('malformed HTTP produces a fixed response without raw bytes or stack trace',async()=>withServer(async port=>{
  const response=await raw(port,'GET /PRIVATE-PROBE HTTP/1.1\r\nBad Header: SECRET\r\n\r\n');
  assert.match(response,/^HTTP\/1\.1 400/);assert.ok(!/PRIVATE-PROBE|SECRET|Error:|serve\.mjs/.test(response));
}));
test('CLI refuses occupied fixed port, alternative-port arguments and never logs request content',async()=>{
  const {createLocalServer}=await load(),blocker=createLocalServer();
  await new Promise((resolve,reject)=>{blocker.once('error',reject);blocker.listen(8768,'127.0.0.1',resolve);});
  const run=args=>new Promise((resolve,reject)=>{const child=spawn(process.execPath,[fileURLToPath(script),...args],{windowsHide:true});let output='';child.stdout.on('data',data=>{output+=data;});child.stderr.on('data',data=>{output+=data;});child.on('error',reject);child.on('close',code=>resolve({code,output}));});
  try {const failure=await run([]);assert.equal(failure.code,1);assert.match(failure.output,/8768/);assert.ok(!/Error:|EADDRINUSE|C:\\|at /.test(failure.output));
    const launcher=fileURLToPath(new URL('../start_local_first.local.cmd',import.meta.url));
    const window=spawn('cmd.exe',['/d','/c',launcher],{windowsHide:true});let text='',closed=false;
    window.stderr.on('data',data=>{text+=data.toString();});
    const exit=new Promise(resolve=>window.once('close',code=>{closed=true;resolve(code);}));
    try{
      await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Launcher did not preserve failure')),5000);
        window.on('error',reject);window.stdout.on('data',data=>{text+=data.toString();if(text.includes('不要改用其他連接埠')){clearTimeout(timer);resolve();}});
      });
      await new Promise(resolve=>setTimeout(resolve,100));assert.equal(closed,false,'Failure window closed before acknowledgement');
      window.stdin.end('\r\n');assert.equal(await exit,1);assert.ok(text.includes('8768'));
    }finally{if(!closed){window.kill();await exit;}}
  }
  finally{await stop(blocker);}
  const alternative=await run(['PRIVATE-PROBE']);assert.equal(alternative.code,1);assert.ok(!alternative.output.includes('PRIVATE-PROBE'));
  const child=spawn(process.execPath,[fileURLToPath(script)],{windowsHide:true});let output='';
  const exited=new Promise(resolve=>child.once('close',resolve));
  const ready=new Promise((resolve,reject)=>{child.on('error',reject);child.stdout.on('data',data=>{output+=data;if(output.includes('http://127.0.0.1:8768/local-first/'))resolve();});child.stderr.on('data',data=>{output+=data;});child.once('close',()=>reject(new Error('Fixed server exited before readiness')));});
  try {await ready;assert.equal((await get(8768)).status,200);await get(8768,'/local-first/?secret=PRIVATE-PROBE',{Host:'SECRET-HOST'});}
  finally{child.kill();await exited;}
  assert.ok(!/PRIVATE-PROBE|SECRET-HOST/.test(output),'Request details logged');
});
