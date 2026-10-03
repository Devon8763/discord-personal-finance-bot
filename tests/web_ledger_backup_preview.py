"""Disposable loopback Web rehearsal; stub login, no real credentials or ledger."""
import os
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / 'tests')]
temp_root = Path(tempfile.gettempdir()).resolve()


def guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if root in path.parents and (path.name.lower() in {'data.db', '.env', 'token', 'token.txt'}
                                    or any(part.lower() in {'backups', 'exports', 'uploads'} for part in path.parts)):
            raise RuntimeError('Protected project data refused')
    if event == 'sqlite3.connect' and args[0] != ':memory:':
        if temp_root not in Path(args[0]).resolve().parents:
            raise RuntimeError('Non-temporary SQLite refused')


sys.addaudithook(guard)

import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import Response, RedirectResponse  # noqa: E402
from test_web_ledger_backup import WebLedgerBackupTests  # noqa: E402
from web.auth import start_session  # noqa: E402

fixture = WebLedgerBackupTests()
fixture.setUp()
app = fixture.client.app


@app.get('/tests/synthetic-login')
async def login(request: Request):
    start_session(request.session, fixture.owner)
    return RedirectResponse('/export/ledger', status_code=303, headers={'Cache-Control': 'no-store'})


@app.get('/tests/ledger-backup-checks')
async def checks(request: Request):
    response = await __import__('web.routes', fromlist=['ledger_backup_page']).ledger_backup_page(request)
    if response.status_code == 200:
        body = response.body.decode().replace('src="/export/ledger/modules/download.mjs"',
                                            'src="/tests/web_ledger_backup_browser.mjs"')
        return Response(body, media_type='text/html', headers=dict(response.headers) | {'content-length': str(len(body.encode()))})
    return response


@app.get('/tests/web_ledger_backup_browser.mjs')
async def script():
    return Response((root / 'tests/web_ledger_backup_browser.mjs').read_bytes(), media_type='text/javascript')


@app.get('/tests/ledger/{name}')
async def ledger_module(name: str):
    if name not in {'ledger.mjs', 'idb.mjs', 'rules.mjs', 'backup.mjs'}:
        return Response(status_code=404)
    return Response((root / 'local-first' / name).read_bytes(), media_type='text/javascript')


@app.get('/tests/ledger/vendor/lossless-json-4.3.1/lossless-json.js')
async def lossless():
    return Response((root / 'local-first/vendor/lossless-json-4.3.1/lossless-json.js').read_bytes(), media_type='text/javascript')


if __name__ == '__main__':
    try:
        uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), access_log=False, log_level='warning')
    finally:
        fixture.doCleanups()
