import argparse,copy,json,datetime,unittest
from common import ROOT,fixture,Ports
from first_followup_integration.basis import project_schedule_basis,ScheduleBasisError
import b806_api
class PublicCases(unittest.TestCase):
    def go(self,f=None):return project_schedule_basis(**(fixture() if f is None else f))
    def reject(self,f,code=None):
        with self.assertRaises(ScheduleBasisError) as caught:self.go(f)
        self.assertIn(caught.exception.code,{'INVALID_INPUT','DEPENDENCY_REJECTED','SCOPE_MISMATCH','SOURCE_UNCONFIRMED','FENCED'})
        if code:self.assertEqual(caught.exception.code,code)
        self.assertEqual(str(caught.exception),caught.exception.code)
    def test_positive_exact(self):self.assertEqual(self.go(),json.loads((ROOT/'fixtures/expected.json').read_text()))
    def test_no_mutation(self):
        f=fixture();before=copy.deepcopy(f);a=self.go(f);b=self.go(f);self.assertEqual(f,before);self.assertEqual(a,b);a['source_state']='x';self.assertEqual(self.go(f),b)
    def test_consumer(self):
        f=fixture();out=self.go(f);self.assertEqual(set(out),b806_api.BASIS_FIELDS)
        p=Ports(out,f['scope']);service=b806_api.Service(p)
        request={'scope':f['scope'],'policy_version':f['policy_version'],'source_action_id':f['source_action_id'],'ordinal':1,'due_at':'2026-10-20T11:00:00Z'}
        self.assertEqual(service.execute('schedule',request)['status'],'OK')
        self.assertEqual(service.execute('schedule',request)['status'],'REPLAY')
        self.assertEqual(len(p.calls),1)
    def test_consumer_too_early(self):
        f=fixture();p=Ports(self.go(f),f['scope']);q={'scope':f['scope'],'policy_version':f['policy_version'],'source_action_id':f['source_action_id'],'ordinal':1,'due_at':'2026-10-20T10:59:59.999999Z'}
        self.assertEqual(b806_api.Service(p).execute('schedule',q)['status'],'INVALID');self.assertEqual(p.rows,{})
    def test_accepted_stays_accepted(self):
        f=fixture();f['receipt']['outcome']='ACCEPTED';self.assertEqual(self.go(f)['source_state'],'ACCEPTED')
    def test_unconfirmed_outcomes(self):
        for outcome in ['TIMEOUT','UNKNOWN']:
            with self.subTest(outcome=outcome):
                f=fixture();f['receipt'].update(outcome=outcome,provider_message_id=None,evidence_ref=None);self.reject(f,'SOURCE_UNCONFIRMED')
        f=fixture();f['receipt']=None;self.reject(f,'SOURCE_UNCONFIRMED')
        f=fixture();f['receipt'].update(outcome='NOT_SENT',provider_message_id=None,not_sent_reason='PROVIDER_REJECTED_BEFORE_ACCEPTANCE');self.reject(f,'SOURCE_UNCONFIRMED')
    def test_scope_each_field(self):
        for k in ['client_id','campaign_id','lead_id','mailbox_ref','thread_id']:
            with self.subTest(field=k):
                f=fixture();f['scope'][k]='22222222-2222-4222-8222-222222222222' if k=='campaign_id' else 'other';self.reject(f,'SCOPE_MISMATCH')
    def test_identity_locale_versions(self):
        for obj,key,val in [('action','action_id','other'),('action','policy_version','other'),('action','locale','en-GB'),('action','language_pack_version','other')]:
            f=fixture();f[obj][key]=val;self.reject(f,'SCOPE_MISMATCH')
    def test_receipt_payload_scope_identity(self):
        for key,val in [('action_id','other'),('client_id','other'),('payload_sha256','f'*64)]:
            f=fixture();f['receipt'][key]=val;self.reject(f,'DEPENDENCY_REJECTED')
    def test_valid_receipt_wrong_action(self):
        f=fixture();f['attempt']['action_id']=f['receipt']['action_id']='other';self.reject(f,'SCOPE_MISMATCH')
    def test_fences(self):
        for status in ['STOPPED','HANDED_OFF']:
            f=fixture();f['state']['status']=status;self.reject(f,'FENCED')
    def test_numeric_boundaries(self):
        for obj,key in [('state','followups_sent'),('policy','max_followups'),('policy','cooldown_seconds')]:
            for val in [True,-1,1.5,'2',None]:
                with self.subTest(obj=obj,key=key,val=val):
                    f=fixture();f[obj][key]=val;self.reject(f)
        f=fixture();f['policy']['max_followups']=0;self.assertEqual(self.go(f)['max_followups'],0)
        f=fixture();f['state']['followups_sent']=99;self.assertEqual(self.go(f)['followups_sent'],99)
    def test_time_offsets(self):
        f=fixture();f['state']['last_outbound_at']='2026-10-20T12:00:00.123456+02:00';self.assertEqual(self.go(f)['last_outbound_at'],'2026-10-20T10:00:00.123456Z')
    def test_bad_times(self):
        for val in [None,'2026-10-20T13:00:00Z','2026-10-20T10:00:00','2026-10-20T10:00:00-00:00','2026-10-20T10:00:00.1234567Z','2026-02-30T10:00:00Z']:
            with self.subTest(value=val):
                f=fixture();f['state']['last_outbound_at']=val;self.reject(f)
        f=fixture();f['now']=datetime.datetime(2026,10,20);self.reject(f)
    def test_overflow(self):
        f=fixture();f['policy']['cooldown_seconds']=10**50;self.reject(f,'SOURCE_UNCONFIRMED')
    def test_payload_hash(self):
        f=fixture();f['action']['payload']['body']='changed';self.reject(f,'DEPENDENCY_REJECTED')
    def test_scope_shape(self):
        for val in [None,[],True,{},dict(fixture()['scope'],extra='x')]:
            f=fixture();f['scope']=val;self.reject(f,'INVALID_INPUT')
    def test_utf8_identifier(self):
        f=fixture();f['scope']['client_id']='bad\ud800';self.reject(f,'INVALID_INPUT')
    def test_no_implicit_policy(self):
        f=fixture();del f['policy']['language_pack_versions'];self.reject(f,'DEPENDENCY_REJECTED')
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',action='store_true');args=ap.parse_args()
    if args.baseline:
        try:project_schedule_basis(**fixture())
        except NotImplementedError as e:
            if str(e)!='MISSING_IMPLEMENTATION':raise
            print('BASELINE_EXPECTED_MISSING_IMPLEMENTATION');raise SystemExit(0)
        raise SystemExit('Baseline expected unimplemented target; no implementation PASS claimed')
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PublicCases))
    raise SystemExit(0 if result.testsRun>0 and result.wasSuccessful() and not result.skipped and not result.expectedFailures else 1)
