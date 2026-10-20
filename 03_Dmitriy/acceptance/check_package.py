import json,hashlib,copy,datetime
from common import ROOT,fixture,Ports
manifest=json.loads((ROOT/'PROTECTED_SHA256.json').read_text())
for name,digest in manifest.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
from sales_first_decision import decide_next_action
from sales_outbound_intent import build_outbound_intent
from sales_first_send_receipt import normalize_send_receipt
import b806_api
f=fixture();s=f['state'];event={'schema_version':'FIRST_DIALOGUE_EVENT_V1','event_id':'p41-validation',**{k:s[k] for k in ('client_id','runtime_campaign_id','lead_id','mailbox_ref','thread_id')},'kind':'AUTOREPLY','observed_at':f['now'].isoformat()}
before=copy.deepcopy(f)
assert decide_next_action(s,event,f['policy'],now=f['now'])['action']=='NOOP'
assert build_outbound_intent(f['action'])['state']=='PREPARED'
assert normalize_send_receipt(f['attempt'],f['receipt'])['state']=='DELIVERED'
expected=json.loads((ROOT/'fixtures/expected.json').read_text());assert set(expected)==b806_api.BASIS_FIELDS
p=Ports(expected,f['scope']);req={'scope':f['scope'],'policy_version':f['policy_version'],'source_action_id':f['source_action_id'],'ordinal':1,'due_at':'2026-10-20T11:00:00Z'}
assert b806_api.Service(p).execute('schedule',req)['status']=='OK'
assert b806_api.Service(p).execute('schedule',req)['status']=='REPLAY'
assert f==before
print(json.dumps({'preparation':'PASS','protected_files':len(manifest),'original_APIs':'F103/F114/F108 invoked unchanged','consumer':'accepted B806 public execute OK/REPLAY with synthetic ports only','implementation':'NOT_IMPLEMENTED'},ensure_ascii=False))
