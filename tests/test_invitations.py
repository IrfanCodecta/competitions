"""Exercise wire delivery, recipient persistence, and organizer acceptance."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('APP_STORAGE_DIR', tempfile.gettempdir()+'/competitions-test-unused')
os.environ.setdefault('INSTANCE_ORIGIN','https://organizer.example.org')
import service
import invite_retry
import review_job
from domain import Competition, connect, queue_invitation, respond_invitation, save_invitation

class InvitationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.organizer=self.root/'organizer.sqlite3';self.recipient=self.root/'recipient.sqlite3'
  with connect(self.organizer) as db:
   engine=Competition(db,'@organizer',admin=True)
   self.cid=engine.run('create',{'title':'Test','description':'Brief','start':'2026-01-01','end':'2099-01-01'})['id']
  self.payload={'id':'f'*40,'sender':'organizer.example.org','organizer':'@organizer','organizer_host':'organizer.example.org','invitee':'@alice','challenge':self.cid,'title':'Test','description':'Brief','start':'2026-01-01','end':'2099-01-01'}
 def tearDown(self):self.tmp.cleanup()
 async def receive(self,method,url,**kwargs):
  if 'old.example.org' in url:return httpx.Response(404,json={'detail':'Public app service not found.'})
  # Public HTTP gateway adds the envelope. Caller sends ONLY the payload.
  req={'schema':1,'method':method,'path':'invitation','public':True,'actor':{'scope':'public'},'body':json.loads(json.dumps(kwargs['json']))}
  with patch.object(service,'DB_PATH',self.recipient),patch.object(service,'profile',AsyncMock(return_value={'handle':'@alice'})),patch.object(service,'directory',AsyncMock(return_value=['organizer.example.org'])):
   result=await service.main(req)
  return httpx.Response(200,json=result)
 def test_retry_delivers_plain_body_to_second_host(self):
  dbpath=self.root/'competitions.sqlite3'
  with connect(dbpath) as db:queue_invitation(db,self.payload)
  with patch.object(invite_retry,'platform_hosts',AsyncMock(return_value=['old.example.org','alice.example.org'])),patch.object(invite_retry,'federation_request',side_effect=self.receive),patch.object(invite_retry,'API','http://platform'),patch.dict(os.environ,{'APP_TOKEN':'test'}):
   invite_retry.run(self.root)
  with connect(dbpath) as db:self.assertEqual(0,db.execute('select count(*) from invite_outbox').fetchone()[0])
  with connect(self.recipient) as db:self.assertEqual(self.payload['id'],Competition(db,'@alice').run('invitations',{})['invitations'][0]['id'])
 def test_immediate_delivery_and_acceptance(self):
  req={'schema':1,'method':'POST','path':'command','public':False,'actor':{'scope':'app'},'body':{'action':'invite','body':{'challenge':self.cid,'handle':'@alice'},'request_id':'a'*32}}
  with patch.object(service,'DB_PATH',self.organizer),patch.object(service,'HOST','organizer.example.org'),patch.object(service,'profile',AsyncMock(return_value={'handle':'@organizer'})),patch.object(service,'directory',AsyncMock(return_value=['old.example.org','alice.example.org'])),patch.object(service,'federation_request',side_effect=self.receive):
   asyncio.run(service.main(req))
  with connect(self.recipient) as db:inv=Competition(db,'@alice').run('invitations',{})['invitations'][0]
  with connect(self.organizer) as db:
   self.assertEqual(0,db.execute('select count(*) from invite_outbox').fetchone()[0])
   c=Competition(db,'@organizer',admin=True).get(self.cid);self.assertNotIn('@alice',c['competitors'])
   result=Competition(db,'@alice').run('accept_invite',{'challenge':self.cid,'invitation_id':inv['id']})
   self.assertTrue(result['accepted'])
   self.assertIn('@alice',Competition(db,'@alice').get(self.cid)['competitors'])
 def test_legacy_pending_competitor_can_accept_without_duplicate(self):
  with connect(self.organizer) as db:
   e=Competition(db,'@organizer',admin=True);e.run('invite',{'challenge':self.cid,'handle':'@alice','invitation_id':self.payload['id']})
   c=e.get(self.cid);c['competitors'].append('@alice');e.put(c)
   Competition(db,'@alice').run('accept_invite',{'challenge':self.cid,'invitation_id':self.payload['id']})
   self.assertEqual(['@alice'],e.get(self.cid)['competitors'])
 def test_duplicate_delivery_preserves_decline(self):
  with connect(self.recipient) as db:
   save_invitation(db,{**self.payload,'status':'pending'})
   respond_invitation(db,'@alice',self.payload['id'],'declined')
   save_invitation(db,{**self.payload,'status':'pending'})
   self.assertEqual([],Competition(db,'@alice').run('invitations',{})['invitations'])
 def test_job_uses_passed_installation_id_for_both_workers(self):
  with patch.dict(os.environ,{'DATA_DIR':str(self.root),'APP_ID':'15','APP_STORAGE_DIR':'/wrong'}),patch.object(invite_retry,'run') as retry,patch.object(review_job,'run') as review:
   review_job.main(['987'])
   retry.assert_called_once_with(self.root/'apps'/'987');review.assert_called_once_with(self.root/'apps'/'987')

 def test_recipient_response_uses_browser_command_body(self):
  with connect(self.recipient) as db:save_invitation(db,{**self.payload,'status':'pending'})
  req={'schema':1,'method':'POST','path':'command','public':False,'actor':{'scope':'app'},'body':{'action':'respond_invitation','body':{'invitation_id':self.payload['id'],'status':'accepted'},'request_id':'b'*32}}
  with patch.object(service,'DB_PATH',self.recipient),patch.object(service,'profile',AsyncMock(return_value={'handle':'@alice'})):
   result=asyncio.run(service.main(req));self.assertEqual('accepted',result['status'])
 def test_acceptance_recovery_is_idempotent(self):
  with connect(self.organizer) as db:
   e=Competition(db,'@organizer',admin=True);e.run('invite',{'challenge':self.cid,'handle':'@alice','invitation_id':self.payload['id']})
   actor=Competition(db,'@alice');body={'challenge':self.cid,'invitation_id':self.payload['id']}
   self.assertEqual(actor.run('accept_invite',body),actor.run('accept_invite',body))
   self.assertEqual(['@alice'],e.get(self.cid)['competitors'])
