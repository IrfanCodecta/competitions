import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from domain import Competition,Problem,connect,fields,score
from review_job import run,parse_response
from PIL import Image

class DomainTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'competitions.sqlite3';self.db=connect(self.path)
  self.admin=Competition(self.db,'@organizer',admin=True,today='2026-09-23')
  self.cid=self.admin.run('create',{'title':'Test challenge','description':'Requirements','start':'2026-09-01','end':'2026-09-30'})['id']
  for who in ['@alice','@bob']:self.call(self.admin,'invite',handle=who)
  self.kid=self.card('human',cap=2)
  self.call(self.admin,'publish');self.db.commit()
  self.alice=Competition(self.db,'@alice',today='2026-09-23');self.bob=Competition(self.db,'@bob',today='2026-09-23')
  self.reviewer=Competition(self.db,'@reviewer',today='2026-09-23')
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def call(self,actor,action,**body):return actor.run(action,{'challenge':self.cid,'card':getattr(self,'kid',None),**body})
 def card(self,mode,cap=None,reviewers=None):
  c=self.call(self.admin,'add_card',name='Card',requirements='Make useful work.',types=['text'],cap=cap,review=mode,reviewers=reviewers or ['@reviewer'],ai_consent=mode=='agent')
  return c['cards'][-1]['id']
 def submit(self,actor=None,**body):return self.call(actor or self.alice,'submit',values={'text':'My entry'},**body)
 def approve(self,s,actor=None):return self.call(actor or self.reviewer,'decide',submission=s['id'],decision='approved')
 def fails(self,status,fn):
  with self.assertRaises(Problem) as ctx:fn()
  self.assertEqual(status,ctx.exception.status)
 def test_uninvited_cannot_view_submit_score_or_create(self):
  outsider=Competition(self.db,'@outsider',today='2026-09-23')
  self.assertEqual([],outsider.run('list',{})['challenges'])
  for action in ['challenge','card','people','queue','leaderboard','submit','score','invite']:
   self.fails(403,lambda:self.call(outsider,action))
  self.fails(403,lambda:outsider.run('create',{}))
 def test_competitor_cannot_admin_or_review(self):
  for action in ['invite','add_card','publish','leaderboard','queue','decide','score']:
   self.fails(403,lambda:self.call(self.alice,action))
 def test_no_roster_leak_in_main_views(self):
  self.submit(self.bob)
  summary=self.call(self.alice,'challenge');card=self.call(self.alice,'card')
  self.assertNotIn('competitors',summary);self.assertNotIn('@bob',json.dumps(card));self.assertNotIn('@reviewer',json.dumps(summary))
  people=self.call(self.alice,'people')['people'];self.assertEqual({'@alice','@bob'},{p['handle'] for p in people})
 def test_rejected_counts_and_concurrent_cap(self):
  s=self.submit();self.call(self.reviewer,'decide',submission=s['id'],decision='rejected')
  self.submit();self.fails(409,lambda:self.submit())
 def test_exact_fields_and_complete_pr(self):
  self.fails(400,lambda:fields(['text'],{'text':'ok','link':'https://example.org'}))
  self.fails(400,lambda:fields(['text'],{'text':' '}))
  self.fails(400,lambda:fields(['pr'],{'pr':{'url':'https://example.org'}}))
  self.fails(400,lambda:fields(['link'],{'link':'javascript:alert(1)'}))
  self.fails(400,lambda:fields(['image'],{'image':{'name':'x.png','data':'data:image/png;base64,YWJj'}}))
  raw=io.BytesIO();Image.new('RGB',(2,2),'red').save(raw,format='PNG');im={'name':'x.png','data':'data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()}
  v=fields(['pr','image'],{'image':im,'pr':{'url':'https://example.org/pr/1','before':im,'after':im}});self.assertEqual(v['pr']['before'],im)
 def test_card_validation(self):
  base={'name':'x','requirements':'r','types':['text'],'cap':None,'review':'human','reviewers':['@reviewer']}
  for patch in [{'types':[]},{'requirements':' '},{'cap':0},{'cap':1.5},{'cap':True},{'reviewers':[]},{'review':'agent','ai_consent':False}]:
   self.fails(400,lambda:self.call(self.admin,'add_card',**(base|patch)))
 def test_dates_and_draft_gate(self):
  early=Competition(self.db,'@alice',today='2026-08-30');late=Competition(self.db,'@alice',today='2026-10-01')
  for a in [early,late]:self.fails(409,lambda:self.submit(a))
  self.fails(400,lambda:self.admin.run('create',{'title':'x','description':'x','start':'2026-09-30','end':'2026-09-01'}))
 def test_multi_reviewer_all_approve_any_reject(self):
  self.kid=self.card('human',reviewers=['@reviewer','@second']);s=self.submit()
  self.assertEqual('pending',self.approve(s)['status'])
  second=Competition(self.db,'@second',today='2026-09-23')
  self.assertEqual('approved',self.approve(s,second)['status'])
  s=self.submit();self.assertEqual('rejected',self.call(second,'decide',submission=s['id'],decision='rejected')['status'])
 def test_official_scores_sum_and_pending(self):
  s=self.submit();self.fails(409,lambda:self.call(self.admin,'score',person='@alice',score='5.7'));self.approve(s)
  self.call(self.admin,'score',person='@alice',score='5.7');other=self.card('human');s=self.submit(card=other)
  self.call(self.reviewer,'decide',card=other,submission=s['id'],decision='approved')
  self.call(self.admin,'score',card=other,person='@alice',score='2.4')
  rows=self.call(self.admin,'leaderboard')['rows'];self.assertEqual(8.1,rows[0]['total']);self.assertIsNone(rows[1]['total']);self.assertTrue(all(v is None for v in rows[1]['scores'].values()))
 def test_score_precision(self):
  for x in ['-1','10.1','5.77','1e0','NaN',True,'',11,None]:self.fails(400,lambda:score(x))
  for x,y in [('0',0),('0.0',0),('5.7',57),('10.0',100)]:self.assertEqual(y,score(x))
 def test_review_gateway_request_has_id_and_bounded_advisory_payload(self):
  self.kid=self.card('agent');self.submit();self.db.commit()
  client=MagicMock();client.get.return_value.json.return_value={'default_model':'test-model'}
  client.post.return_value.json.return_value={'output':[{'type':'message','content':[{'type':'output_text','text':'{"score":7.2,"rationale":"Meets the brief."}'}]}]}
  with patch('review_job.httpx.Client') as factory:
   factory.return_value.__enter__.return_value=client
   run(Path(self.tmp.name))
  client.post.assert_called_once();args,kw=client.post.call_args
  self.assertEqual('/v1/responses',args[0]);self.assertRegex(kw['headers']['X-Mobius-Request-Id'],r'^competitions:[a-f0-9]{32}$')
  self.assertEqual(1500,kw['json']['max_output_tokens']);self.assertEqual([],kw['json']['tools'])
  c=self.admin.get(self.cid);self.assertEqual('pending',c['submissions'][0]['status']);self.assertEqual({},c['scores'])
 def test_agent_never_finalizes_or_sets_official_score(self):
  self.kid=self.card('agent');s=self.submit();self.db.commit()
  self.fails(409,lambda:self.call(self.admin,'decide',submission=s['id'],decision='approved'))
  run(Path(self.tmp.name),call=lambda c,s:(8.4,'Meets the brief.'))
  saved=self.admin.get(self.cid);entry=next(x for x in saved['submissions'] if x['id']==s['id'])
  self.assertEqual('pending',entry['status']);self.assertEqual(8.4,entry['agent_score']);self.assertEqual({},saved['scores'])
  self.assertEqual('approved',self.call(self.admin,'decide',submission=s['id'],decision='approved')['status'])
 def test_agent_error_does_not_fabricate_score(self):
  self.kid=self.card('agent');s=self.submit();self.db.commit()
  def fail(c,s):raise RuntimeError('provider down')
  run(Path(self.tmp.name),call=fail)
  s=self.admin.get(self.cid)['submissions'][0];self.assertIsNone(s['agent_score']);self.assertTrue(s['agent_error']);self.assertEqual('pending',s['status'])
 def test_parse_model_result(self):
  body={'output':[{'type':'message','content':[{'type':'output_text','text':'{"score":5.7,"rationale":"Needs stronger evidence."}'}]}]}
  self.assertEqual((5.7,'Needs stronger evidence.'),parse_response(body))

if __name__=='__main__':unittest.main()
