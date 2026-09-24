"""Competition rules. All mutations are transactional and actor-authorized."""
import base64
import io
import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from PIL import Image

FIELD_TYPES = {'image':'Image upload', 'link':'Link', 'text':'Text', 'pr':'PR link + before/after screenshots'}

class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)

def require(ok, message, status=400):
    if not ok: raise Problem(status, message)

def text(value, label, limit=10000):
    require(isinstance(value,str) and 0 < len(value.strip()) <= limit, f'{label} is required (up to {limit} characters).')
    return value.strip()

def handle(value):
    value = text(value,'Mobius ID',64).lower().lstrip('@')
    require(bool(re.fullmatch(r'[a-z0-9_]{3,30}',value)), 'Enter a Mobius @handle, not a display name.')
    return '@'+value

def url(value):
    value=text(value,'Link',2000)
    p=urlsplit(value)
    require(p.scheme in ('http','https') and bool(p.hostname) and not p.username and not p.password,'Enter a complete http or https link.')
    return value

def image(value):
    require(isinstance(value,dict) and set(value)=={'name','data'},'Choose an image file.')
    name=text(value['name'],'Image filename',200)
    require(isinstance(value['data'],str) and len(value['data']) < 2_800_000,'Images must be under 2 MB.')
    m=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)',value['data'])
    require(m is not None,'Use PNG, JPEG, or WebP images.')
    try:
        raw=base64.b64decode(m[2],validate=True)
        require(0<len(raw)<=2_000_000,'Images must be under 2 MB.')
        with Image.open(io.BytesIO(raw)) as im:
            require(im.format.lower() in ('png','jpeg','webp') and im.width*im.height<=24_000_000,'Image is too large or unsupported.')
            im.verify()
    except Problem: raise
    except Exception: raise Problem(400,'That file could not be read as an image.')
    return {'name':name,'data':value['data']}

def fields(required, values):
    require(isinstance(values,dict) and set(values)==set(required),'Fill exactly the fields required by this card.')
    out={}
    for kind in required:
        v=values[kind]
        if kind=='image': out[kind]=image(v)
        elif kind=='text': out[kind]=text(v,'Text submission',20000)
        elif kind=='link': out[kind]=url(v)
        elif kind=='pr':
            require(isinstance(v,dict) and set(v)=={'url','before','after'},'A PR link needs both before and after screenshots.')
            out[kind]={'url':url(v['url']),'before':image(v['before']),'after':image(v['after'])}
        else: raise Problem(400,'Unsupported field type.')
    require(len(json.dumps(out))<7_000_000,'Combined attachments are too large. Use smaller images.')
    return out

def score(value):
    require(not isinstance(value,bool) and isinstance(value,(str,int,float)), 'Enter a score from 0.0 to 10.0.')
    s=str(value)
    require(bool(re.fullmatch(r'(?:10(?:\.0)?|[0-9](?:\.[0-9])?)',s)), 'Use 0.0–10.0 with at most one decimal place.')
    return int(Decimal(s)*10)

def connect(path):
    db=sqlite3.connect(path, timeout=8)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY, document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS proofs(id TEXT PRIMARY KEY, document TEXT NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS hosts(host TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY, document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS invite_outbox(id TEXT PRIMARY KEY, document TEXT NOT NULL, next_attempt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
    ''')
    return db

def save_invitation(db, invitation):
    db.execute('INSERT INTO invitations(id,document) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',(invitation['id'],json.dumps(invitation)))

def list_invitations(db, actor):
    out=[]
    for row in db.execute('SELECT document FROM invitations'):
        item=json.loads(row[0])
        if item.get('invitee')==actor and item.get('status','pending')=='pending': out.append(item)
    return out

def respond_invitation(db, actor, invitation_id, status):
    row=db.execute('SELECT document FROM invitations WHERE id=?',(invitation_id,)).fetchone()
    require(row is not None,'Invitation not found.',404)
    item=json.loads(row[0]);require(item.get('invitee')==actor,'This invitation is not for you.',403)
    require(status in ('accepted','declined'),'Choose accept or decline.')
    item['status']=status;db.execute('UPDATE invitations SET document=? WHERE id=?',(json.dumps(item),invitation_id));return item

def queue_invitation(db, invitation):
    db.execute('INSERT OR IGNORE INTO invite_outbox(id,document,next_attempt,attempts) VALUES(?,?,?,0)',(invitation['id'],json.dumps(invitation),0.0))

class Competition:
    def __init__(self,db,actor,*,admin=False,today=None):
        self.db,self.actor,self.admin=db,actor,admin
        self.today=today or datetime.now(timezone.utc).date().isoformat()
    def get(self,cid):
        row=self.db.execute('SELECT document FROM challenges WHERE id=?',(cid,)).fetchone()
        require(row is not None,'Challenge not found.',404)
        return json.loads(row[0])
    def put(self,c):
        self.db.execute('INSERT INTO challenges VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',(c['id'],json.dumps(c)))
    def is_competitor(self,c):return self.actor in c['competitors'] and not any(x.get('handle')==self.actor and x.get('status')=='pending' for x in c.get('invitations',[]))
    def is_reviewer(self,card):return self.actor in card['reviewers']
    def visible(self,c):return self.admin or self.is_competitor(c) or any(self.is_reviewer(x) for x in c['cards'])
    def card(self,c,kid):
        card=next((x for x in c['cards'] if x['id']==kid),None)
        require(card is not None,'Card not found.',404)
        return card
    def summary(self,c):
        out={k:c[k] for k in ('id','title','description','start','end','cover','published')}
        out['admin']=self.admin
        out['competitor']=self.is_competitor(c)
        out['cards']=[{k:v for k,v in card.items() if k!='reviewers'}|{'can_review':self.admin or self.is_reviewer(card)} for card in c['cards'] if self.admin or (c['published'] and (self.is_competitor(c) or self.is_reviewer(card)))]
        if self.admin: out['competitors']=[who for who in c['competitors'] if not any(x.get('handle')==who and x.get('status')=='pending' for x in c.get('invitations',[]))]
        if self.admin: out['invitations']=c.get('invitations',[])
        return out
    def run(self,action,b):
        require(isinstance(b,dict),'Invalid request.')
        if action=='invitations': return {'invitations':list_invitations(self.db,self.actor)}
        if action=='list':
            return {'challenges':[dict(self.summary(c),cover=None) for row in self.db.execute('SELECT document FROM challenges') if self.visible(c:=json.loads(row[0])) and (self.admin or c['published'])], 'actor':self.actor,'admin':self.admin,'field_types':FIELD_TYPES}
        if action=='create':
            require(self.admin,'Only the organizer can create challenges.',403)
            start,end=b.get('start'),b.get('end')
            try: require(date.fromisoformat(start)<=date.fromisoformat(end),'End date must not be before start date.')
            except (ValueError,TypeError): raise Problem(400,'Choose valid start and end dates.')
            c={'id':uuid.uuid4().hex,'title':text(b.get('title'),'Title',160),'description':text(b.get('description'),'Description'),'start':start,'end':end,'cover':image(b['cover']) if b.get('cover') else None,'published':False,'competitors':[],'invitations':[],'cards':[],'submissions':[],'scores':{}}
            self.put(c);return self.summary(c)
        c=self.get(b.get('challenge'))
        if action in ('accept_invite','decline_invite'):
            require(not self.admin,'Only an invited competitor can respond to this invitation.',403)
            inv=next((x for x in c.setdefault('invitations',[]) if x.get('id')==b.get('invitation_id') and x.get('handle')==self.actor),None)
            require(inv is not None and inv.get('status')=='pending','This invitation is no longer available.',409)
            if action=='accept_invite':
                require(self.actor not in c['competitors'],'You are already in this challenge.',409)
                inv['status']='accepted';c['competitors'].append(self.actor)
            else: inv['status']='declined'
            self.put(c);return {'accepted':action=='accept_invite','invitation_id':inv['id']}
        require(self.visible(c) and (self.admin or c['published']),'You are not invited to this challenge.',403)
        if action=='challenge': return self.summary(c)
        if action in ('invite','publish','add_card'):
            require(self.admin,'Only the organizer can change this challenge.',403)
            if action=='invite':
                who=handle(b.get('handle'))
                require(who not in c['competitors'],'That competitor is already added.',409)
                invitations=c.setdefault('invitations',[])
                require(not any(x.get('handle')==who and x.get('status')=='pending' for x in invitations),'That competitor already has a pending invitation.',409)
                if b.get('invitation_id'):
                    invitations.append({'id':text(b.get('invitation_id'),'Invitation ID',80),'handle':who,'organizer':self.actor,'status':'pending','created_at':datetime.now(timezone.utc).isoformat()})
                    c['competitors'].append(who)
                else:
                    c['competitors'].append(who)
            elif action=='publish':
                require(len(c['cards'])>0,'Add at least one card before opening the challenge.')
                require(bool(c['competitors'] or c.get('invitations')),'Send at least one competitor invitation before opening the challenge.')
                c['published']=True
            else:
                types=b.get('types')
                require(isinstance(types,list) and bool(types) and all(isinstance(t,str) and t in FIELD_TYPES for t in types) and len(set(types))==len(types),'Choose at least one supported field type, without duplicates.')
                cap=b.get('cap')
                require(cap is None or (type(cap) is int and cap>=1),'Submission limit must be a positive whole number or unlimited.')
                mode=b.get('review')
                require(mode in ('agent','human'),'Choose agent or human review.')
                require(mode!='agent' or b.get('ai_consent') is True,'Confirm use of your Möbius model credit for agent reviews.')
                reviewers=b.get('reviewers',[])
                require(isinstance(reviewers,list),'Reviewer list is invalid.')
                reviewers=list(dict.fromkeys(handle(x) for x in reviewers)) if mode=='human' else []
                require(mode!='human' or len(reviewers)>0,'Assign at least one human reviewer.')
                c['cards'].append({'id':uuid.uuid4().hex,'name':text(b.get('name'),'Card name',160),'requirements':text(b.get('requirements'),'Requirements'),'types':types,'cap':cap,'review':mode,'reviewers':reviewers,'ai_consent':b.get('ai_consent') is True})
            self.put(c);return self.summary(c)
        if action=='leaderboard':
            require(self.admin,'The leaderboard is for the organizer only.',403)
            rows=[]
            for who in c['competitors']:
                values={x['id']:c['scores'].get(x['id'],{}).get(who) for x in c['cards']}
                present=[v for v in values.values() if v is not None]
                rows.append({'handle':who,'scores':{k:v/10 if v is not None else None for k,v in values.items()},'total':sum(present)/10 if present else None,'scored':len(present)})
            rows.sort(key=lambda r:(r['total'] is None,-(r['total'] or 0),r['handle']))
            return {'rows':rows,'cards':c['cards']}
        card=self.card(c,b.get('card'))
        require(self.admin or self.is_competitor(c) or self.is_reviewer(card),'This card is not assigned to you.',403)
        subs=[s for s in c['submissions'] if s['card']==card['id']]
        if action=='card':
            result={'card':{k:v for k,v in card.items() if self.admin or k!='reviewers'},'submissions':[self.entry(s,reference=self.admin or self.is_reviewer(card)) for s in subs if s['competitor']==self.actor], 'can_submit':self.is_competitor(c) and c['published'] and c['start']<=self.today<=c['end'],'can_review':self.admin or self.is_reviewer(card),'can_decide':self.admin if card['review']=='agent' else self.is_reviewer(card),'admin':self.admin,'competitor':self.is_competitor(c)}
            return result
        if action=='submission':
            s=next((s for s in subs if s['id']==b.get('submission')),None)
            require(s is not None,'Submission not found.',404)
            return self.entry(s,reference=self.admin or self.is_reviewer(card),full=True)
        if action in ('people','person'):
            require(self.admin or self.is_competitor(c) or self.is_reviewer(card),'Access denied.',403)
            if action=='people':return {'people':[{'handle':who,'count':sum(s['competitor']==who for s in subs),'score':c['scores'].get(card['id'],{}).get(who)/10 if self.admin and who in c['scores'].get(card['id'],{}) else None} for who in c['competitors']]}
            who=b.get('person');require(who in c['competitors'],'Competitor not found.',404)
            return {'handle':who,'submissions':[self.entry(s,reference=self.admin or self.is_reviewer(card)) for s in subs if s['competitor']==who]}
        if action=='submit':
            require(self.is_competitor(c),'Only invited competitors can submit.',403)
            require(c['published'] and c['start']<=self.today<=c['end'],'This challenge is not accepting submissions.',409)
            count=sum(s['competitor']==self.actor for s in subs)
            require(card['cap'] is None or count<card['cap'],'You have reached this card’s submission limit. Rejected entries still count.',409)
            s={'id':uuid.uuid4().hex,'card':card['id'],'competitor':self.actor,'values':fields(card['types'],b.get('values')),'status':'pending','created_at':datetime.now(timezone.utc).isoformat(),'reviews':{},'agent_score':None,'agent_rationale':None}
            c['submissions'].append(s);self.put(c);return self.entry(s)
        if action=='queue':
            require(self.admin or (card['review']=='human' and self.is_reviewer(card)),'Only assigned reviewers can review this card.',403)
            return {'submissions':[self.entry(s,reference=True) for s in subs if s['status']=='pending']}
        if action=='decide':
            require(self.admin if card['review']=='agent' else self.is_reviewer(card),'Only the assigned human reviewer, or the admin for agent cards, may decide.',403)
            s=next((s for s in subs if s['id']==b.get('submission')),None)
            require(s is not None,'Submission not found.',404)
            require(s['status']=='pending','This submission has already been decided.',409)
            decision=b.get('decision');require(decision in ('approved','rejected'),'Choose approve or reject.')
            comment=b.get('comment','');require(isinstance(comment,str) and len(comment)<=2000,'Comment must be under 2,000 characters.')
            if card['review']=='agent':
                require(s['agent_score'] is not None,'Wait for the agent’s score before the final decision.',409)
                s['status']=decision
            else:
                s['reviews'][self.actor]={'decision':decision,'comment':comment}
                # Explicit human-review rule: all assigned reviewers approve; any rejection rejects.
                decisions=[s['reviews'].get(who,{}).get('decision') for who in card['reviewers']]
                s['status']='rejected' if 'rejected' in decisions else 'approved' if all(x=='approved' for x in decisions) else 'pending'
            s['comment']=comment
            self.put(c);return self.entry(s,reference=True)
        if action=='retry_agent':
            require(self.admin and card['review']=='agent','Only the organizer can retry agent reviews.',403)
            s=next((s for s in subs if s['id']==b.get('submission')),None)
            require(s is not None and s['status']=='pending' and s.get('agent_error'),'No failed review to retry.',409)
            s['agent_error']=None;self.put(c);return {'queued':True}
        if action=='score':
            require(self.admin,'Only the organizer can enter official scores.',403)
            who=b.get('person');require(who in c['competitors'],'Competitor not found.',404)
            require(any(s['competitor']==who and s['status']=='approved' for s in subs),'Approve at least one submission before scoring this competitor.',409)
            c['scores'].setdefault(card['id'],{})[who]=score(b.get('score'))
            self.put(c);return {'saved':True}
        raise Problem(404,'Action not found.')
    @staticmethod
    def entry(s,reference=False,full=False):
        out={k:v for k,v in s.items() if k not in ('agent_score','agent_rationale','reviews')}
        if not full:out.pop('values',None)
        if reference:out.update({k:s[k] for k in ('agent_score','agent_rationale','reviews')})
        return out
