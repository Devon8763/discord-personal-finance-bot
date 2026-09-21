"""Manual local-model test; fabricated summary data only."""
import ast
import json
import sys
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from presentation import SUMMARY_SCHEMA, summary_text
tree = ast.parse(Path('spending_commands.py').read_text(encoding='utf-8'))
prompt = next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ANALYST' for t in n.targets))
payload = dict(model='qwen3:8b',stream=False,think=False,format=SUMMARY_SCHEMA,options=dict(num_predict=500),messages=[dict(role='system',content=prompt),dict(role='user',content=json.dumps({'unit':'月','test_only':True,'current_month':{'total':150,'categories':{'餐飲':{'amount':150,'share':100}}},'comparison':'前月無紀錄，不能推論增減'},ensure_ascii=False))])
request = urllib.request.Request('http://127.0.0.1:11434/api/chat',json.dumps(payload).encode(),{'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=120) as response:
    raw = json.load(response)['message']['content']
text = summary_text(raw)
print(text)
assert '當週' not in text
assert len(text) <= 200
print('Compact summary smoke test passed')
