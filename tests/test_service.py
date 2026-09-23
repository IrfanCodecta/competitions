import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch
import uuid
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# Test-only process environment; never the live installation's data.
_sandbox=tempfile.TemporaryDirectory()
os.environ.setdefault('APP_STORAGE_DIR',_sandbox.name)
os.environ.setdefault('INSTANCE_ORIGIN','https://organizer.example.org')
import service
from domain import Problem,connect

class ServiceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'competitions.sqlite3';self.p=patch.object(service,'DB_PATH',self.path);self.p.start()
  self.cid=self.call('create',{'title':'Test','description':'Brief','start':'2026-01-01','end':'2099-01-01'})['id']
  self.call('invite',{'handle':'@alice'});c=self.call('add_card',{'name':'Card','requirements':'Do work','types':['text'],'cap':1,'review':'human','reviewers':['@reviewer']});self.kid=c['cards'][-1]['id'];self.call('publish',{})
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def call(self,action,body,actor='@organizer',admin=True,rid=None):return service.execute(action,{'challenge':getattr(self,'cid',None),**body},actor,admin,rid or uuid.uuid4().hex)
 def envelope(self,action='submit',body=None):
  command={'action':action,'body':body or {'challenge':self.cid,'card':self.kid,'values':{'text':'Good entry'}},'actor':'@alice','request_id':uuid.uuid4().hex}
  envelope={'schema':1,'method':'POST','path':'exchange','public':True,'body':{'sender':'alice.example.org','proof':'a'*64,'request':command}}
  proof={'digest':service.digest(command),'target':service.HOST,'actor':'@alice','expires':time.time()+100}
  return envelope,proof
 def test_remote_proof_authorizes_only_bound_competitor(self):
  env,proof=self.envelope()
  with patch.object(service,'directory',AsyncMock(return_value=['alice.example.org'])),patch.object(service,'peer',AsyncMock(return_value=proof)):
   result=asyncio.run(service.main(env));self.assertEqual('@alice',result['competitor'])
   again=asyncio.run(service.main(env));self.assertEqual(result['id'],again['id'])
 def test_unregistered_host_rejected_before_callback(self):
  env,proof=self.envelope()
  with patch.object(service,'directory',AsyncMock(return_value=['real.example.org'])),patch.object(service,'peer',AsyncMock(return_value=proof)) as peer:
   with self.assertRaises(Problem) as ctx:asyncio.run(service.main(env))
   self.assertEqual(403,ctx.exception.status);peer.assert_not_called()
 def test_modified_body_target_and_expired_proof_rejected(self):
  for changes in [{'digest':'wrong'},{'target':'wrong.example.org'},{'actor':'@other'},{'expires':time.time()-1}]:
   env,proof=self.envelope();proof.update(changes)
   with patch.object(service,'directory',AsyncMock(return_value=['alice.example.org'])),patch.object(service,'peer',AsyncMock(return_value=proof)):
    with self.assertRaises(Problem) as ctx:asyncio.run(service.main(env))
    self.assertEqual(403,ctx.exception.status)
 def test_verified_remote_cannot_admin(self):
  env,proof=self.envelope('score',{'challenge':self.cid,'card':self.kid,'person':'@alice','score':'10'})
  with patch.object(service,'directory',AsyncMock(return_value=['alice.example.org'])),patch.object(service,'peer',AsyncMock(return_value=proof)):
   with self.assertRaises(Problem) as ctx:asyncio.run(service.main(env))
   self.assertEqual(403,ctx.exception.status)
 def test_concurrent_submissions_enforce_cap_atomically(self):
  def submit(_):
   try:return self.call('submit',{'card':self.kid,'values':{'text':'Work'}},actor='@alice',admin=False)['id']
   except Problem as e:return e.status
  with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(submit,range(4)))
  self.assertEqual(3,results.count(409));self.assertEqual(1,sum(isinstance(x,str) for x in results))
 def test_reused_request_id_cannot_change_body(self):
  rid=uuid.uuid4().hex;self.call('submit',{'card':self.kid,'values':{'text':'Work'}},actor='@alice',admin=False,rid=rid)
  with self.assertRaises(Problem) as ctx:self.call('submit',{'card':self.kid,'values':{'text':'Different'}},actor='@alice',admin=False,rid=rid)
  self.assertEqual(409,ctx.exception.status)
 def test_unknown_public_route_and_proof_fail_closed(self):
  for path in ['command','context','proof/not-valid']:
   with self.assertRaises(Problem):asyncio.run(service.main({'schema':1,'public':True,'path':path,'method':'GET'}))
 def test_private_body_cannot_spoof_identity_or_admin(self):
  # Owner input actor fields are ignored: actor is obtained from platform profile.
  req={'schema':1,'public':False,'path':'command','method':'POST','actor':{'scope':'app'},'body':{'action':'list','actor':'@fake','admin':False,'request_id':uuid.uuid4().hex}}
  with patch.object(service,'profile',AsyncMock(return_value={'handle':'@actual','user_id':'test-user'})):
   result=asyncio.run(service.main(req));self.assertEqual('@actual',result['actor']);self.assertTrue(result['admin'])

if __name__=='__main__':unittest.main()
