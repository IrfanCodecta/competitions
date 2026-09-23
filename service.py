"""Reviewed Competitions service. Browser IDs never grant identity or roles.

Remote authorization uses a short-lived proof on a directory-verified Mobius
host. Proof binds actor, destination, action, exact body digest and request ID.
No owner bearer leaves this instance. Local state writes use SQLite transactions.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import quote, urlsplit
import httpx
from domain import Competition, Problem, connect, handle, require
from peer_transport import federation_request

ROOT=Path(os.environ['APP_STORAGE_DIR'])
ROOT.mkdir(parents=True,exist_ok=True)
DB_PATH=ROOT/'competitions.sqlite3'
SERVICE='/api/app-services/competitions'
ORIGIN=os.environ['INSTANCE_ORIGIN'].rstrip('/')
HOST=urlsplit(ORIGIN).netloc.lower()

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def host(value):
    require(isinstance(value,str) and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',value) and '.' in value,'Enter the organizer’s public hostname.')
    return value

async def platform(method,path,body=None):
    async with httpx.AsyncClient(timeout=5,follow_redirects=False) as client:
        r=await client.request(method,os.environ['API_BASE_URL'].rstrip('/')+path,headers={'Authorization':'Bearer '+os.environ['APP_TOKEN']},json=body)
        if r.status_code>=400:raise Problem(502,'Möbius could not complete the request. Check your account connection.')
        return r.json() if r.content else {}

async def profile():
    d=await platform('GET','/api/identity')
    p=d.get('profile') or {}
    require(not d.get('account_unavailable') and p.get('handle'),'Connect a Möbius account with an @handle in Möbius · You first.',409)
    return {'handle':handle(p['handle']),'user_id':p.get('user_id')}

async def directory(who):
    d=await platform('GET','/api/identity/handles/'+quote(handle(who)[1:],safe=''))
    require(d.get('linked') is True,'Connect your Möbius account to verify invited identities.',409)
    require(bool(d.get('hosts')),'This Mobius ID has no reachable installation.',409)
    return d['hosts']

async def peer(h,path,body=None):
    h=host(h)
    try:
        r=await federation_request('POST' if body is not None else 'GET','https://'+h+SERVICE+'/'+path,json=body,max_response_bytes=8_000_000,timeout_seconds=11)
        d=r.json()
        if r.status_code>=400:raise Problem(r.status_code,d.get('error','The organizer could not accept this request.'))
        return d
    except Problem:raise
    except Exception:raise Problem(502,'The other Möbius installation could not be reached. Nothing has been reported as saved; retry when it is online.')

async def public_request(req):
    path=req['path']; b=req.get('body') or {}
    if path.startswith('proof/') and req['method']=='GET':
        key=path[6:];require(bool(re.fullmatch(r'[a-f0-9]{64}',key)),'Proof not found.',404)
        with connect(DB_PATH) as db:
            row=db.execute('SELECT document,expires FROM proofs WHERE id=?',(key,)).fetchone()
        require(row is not None and row[1]>time.time(),'Proof expired.',403)
        return json.loads(row[0])
    require(path=='exchange' and req['method']=='POST','Not found.',404)
    require(isinstance(b,dict) and set(b)=={'sender','proof','request'},'Invalid remote request.')
    sender=host(b['sender']); pkey=b['proof'];require(isinstance(pkey,str) and re.fullmatch(r'[a-f0-9]{64}',pkey),'Invalid proof.')
    command=b['request'];require(isinstance(command,dict) and set(command)=={'action','body','request_id','actor'},'Invalid command.')
    actor=handle(command['actor'])
    require(sender in await directory(actor),'This host does not belong to that Mobius ID.',403)
    proof=await peer(sender,'proof/'+pkey)
    require(proof.get('digest')==digest(command) and proof.get('target')==HOST and proof.get('actor')==actor and isinstance(proof.get('expires'),(int,float)) and time.time()<proof['expires']<=time.time()+125,'Identity proof does not match this request.',403)
    # Remote actors can NEVER administer the organizer's challenges.
    return execute(command['action'],command['body'],actor,False,command['request_id'])

def execute(action,b,actor,admin,request_id):
    require(isinstance(request_id,str) and re.fullmatch(r'[a-f0-9-]{16,64}',request_id),'A request identifier is required.')
    stamp=digest({'actor':actor,'admin':admin,'action':action,'body':b})
    # Read operations do not retain copies of attachments as replay receipts.
    writes=action in ('create','invite','publish','add_card','submit','decide','score','retry_agent')
    with connect(DB_PATH) as db:
        db.execute('BEGIN IMMEDIATE' if writes else 'BEGIN')
        if writes:
            row=db.execute('SELECT digest,result FROM receipts WHERE id=?',(request_id,)).fetchone()
            if row:
                require(row['digest']==stamp,'That request identifier was already used.',409)
                return json.loads(row['result'])
        result=Competition(db,actor,admin=admin).run(action,b)
        if writes:db.execute('INSERT INTO receipts VALUES(?,?,?)',(request_id,stamp,json.dumps(result)))
        return result

async def main(req):
    require(isinstance(req,dict) and req.get('schema')==1,'Invalid service request.')
    if req.get('public'):return await public_request(req)
    scope=(req.get('actor') or {}).get('scope')
    require(scope in ('owner','app','agent'),'Please open Competitions from your signed-in Möbius.',403)
    p=await profile()
    path=req.get('path');b=req.get('body') or {}
    if path=='context' and req['method']=='GET':
        with connect(DB_PATH) as db:hosts=[r[0] for r in db.execute('SELECT host FROM hosts')]
        return {'identity':p,'host':HOST,'hosts':hosts}
    require(path=='command' and req['method']=='POST','Not found.',404)
    require(isinstance(b,dict),'Invalid command.')
    target=b.get('host') or HOST; action=b.get('action'); body=b.get('body') or {}; rid=b.get('request_id')
    if target==HOST:
        if action=='invite':await directory(body.get('handle'))
        if action=='add_card' and body.get('review')=='human':
            for who in body.get('reviewers',[]):await directory(who)
        return execute(action,body,p['handle'],True,rid)
    target=host(target)
    require(isinstance(rid,str) and re.fullmatch(r'[a-f0-9-]{16,64}',rid),'A request identifier is required.')
    require(HOST in await directory(p['handle']),'Your public installation address is not registered to your Mobius ID.',409)
    command={'action':action,'body':body,'actor':p['handle'],'request_id':rid}
    key=secrets.token_hex(32); expiry=time.time()+120
    proof={'digest':digest(command),'actor':p['handle'],'target':target,'expires':expiry}
    with connect(DB_PATH) as db:
        db.execute('DELETE FROM proofs WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO proofs VALUES(?,?,?)',(key,json.dumps(proof),expiry))
    result=await peer(target,'exchange',{'sender':HOST,'proof':key,'request':command})
    with connect(DB_PATH) as db:db.execute('INSERT OR IGNORE INTO hosts VALUES(?)',(target,))
    return result

if __name__=='__main__':
    try:
        result=asyncio.run(main(json.load(sys.stdin)))
        print(json.dumps({'status':200,'body':result}))
    except Problem as exc:print(json.dumps({'status':exc.status,'body':{'error':exc.message}}))
    except Exception as exc:
        print(type(exc).__name__,file=sys.stderr)
        print(json.dumps({'status':500,'body':{'error':'Competitions could not complete this request. Please retry.'}}))
