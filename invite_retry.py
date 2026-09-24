"""Retry queued competitor invitations without blocking organizer actions."""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import quote
import httpx
from domain import connect
from peer_transport import federation_request

ROOT=Path(os.environ.get('APP_STORAGE_DIR') or '/data/apps/'+os.environ.get('APP_ID','15'))
DB_PATH=ROOT/'competitions.sqlite3'
API=os.environ.get('API_BASE_URL','').rstrip('/')
ORIGIN=os.environ.get('INSTANCE_ORIGIN','').rstrip('/')
HOST=httpx.URL(ORIGIN).host or ''

async def platform_hosts(handle):
    async with httpx.AsyncClient(timeout=8,follow_redirects=False) as client:
        r=await client.get(API+'/api/identity/handles/'+quote(handle.lstrip('@'),safe=''),headers={'Authorization':'Bearer '+os.environ['APP_TOKEN']})
        if r.status_code>=400:return []
        data=r.json();return data.get('hosts',[]) if data.get('linked') is True else []

async def deliver(item):
    payload={k:item[k] for k in ('id','sender','organizer','organizer_host','invitee','challenge','title','description','start','end')}
    for target in await platform_hosts(item['invitee']):
        try:
            envelope={'schema':1,'method':'POST','path':'invitation','public':True,'body':payload}
            response=await federation_request('POST','https://'+target+'/api/app-services/competitions/invitation',json=envelope,max_response_bytes=8000,timeout_seconds=11)
            if response.status_code<400:return True
        except Exception:pass
    return False

def run():
    if not API or not os.environ.get('APP_TOKEN') or not HOST:return
    with connect(DB_PATH) as db:
        rows=db.execute('SELECT id,document,attempts FROM invite_outbox WHERE next_attempt<=? ORDER BY next_attempt LIMIT 10',(time.time(),)).fetchall()
    for row in rows:
        try:item=json.loads(row[1]);ok=asyncio.run(deliver(item))
        except Exception:ok=False
        with connect(DB_PATH) as db:
            if ok: db.execute('DELETE FROM invite_outbox WHERE id=?',(row[0],))
            else:
                attempts=int(row[2])+1
                db.execute('UPDATE invite_outbox SET attempts=?,next_attempt=? WHERE id=?',(attempts,time.time()+min(3600,60*(2**min(attempts,5))),row[0]))

if __name__=='__main__':run()
