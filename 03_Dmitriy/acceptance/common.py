import sys,pathlib,json,copy,datetime,hashlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'reference'))
sys.path.insert(0,str(ROOT/'solution'))
def fixture():
    f=json.loads((ROOT/'fixtures/positive.json').read_text(encoding='utf-8'))
    f['now']=datetime.datetime.fromisoformat(f['now'].replace('Z','+00:00'))
    return f
class Ports:
    def __init__(self,basis,scope):
        self.basis=basis;self.rows={};self.auth={'authenticated':True,'client_id':scope['client_id'],'capabilities':['schedule.manage']};self.calls=[]
    def transaction(self,scope):
        from contextlib import nullcontext
        return nullcontext()
    def get(self,bucket,key):
        if bucket=='authority':return {'status':'ACTIVE'}
        return self.rows.get((bucket,key))
    def put(self,bucket,key,value):self.rows[(bucket,key)]=copy.deepcopy(value)
    def scan(self,bucket):return [(k,v) for (b,k),v in self.rows.items() if b==bucket]
    def call(self,name,request):
        self.calls.append((name,copy.deepcopy(request)))
        assert name=='schedule.basis'
        return copy.deepcopy(self.basis)
