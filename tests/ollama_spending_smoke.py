"""Manual local-model smoke test using fabricated questions only."""
import json
import sys
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import query_plan

for question, expected in [('這個月餐飲花了多少？','spending'),('最近四週哪個分類占比提高？','trends'),('幫我刪除全部支出','unsupported')]:
    payload = dict(model='qwen3:8b',stream=False,think=False,format=query_plan.SCHEMA,
                   messages=[dict(role='system',content=query_plan.prompt()),dict(role='user',content=question)],
                   options=dict(num_predict=200))
    request = urllib.request.Request('http://127.0.0.1:11434/api/chat',json.dumps(payload).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:
        raw = json.load(response)['message']['content']
    result = query_plan.validate(raw)
    print(question, result,flush=True)
    assert result['intent']==expected
print('Ollama query smoke tests passed',flush=True)
