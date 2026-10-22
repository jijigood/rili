import csv,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from calendar_engine import Engine,CalendarError
root=Path(__file__).parents[1]; e=Engine(root/'data')
rows=list(csv.DictReader((root/'sample_cases.csv').open(encoding='utf-8-sig')))
for r in rows:
 x=e.calculate(r['submit_time'],r['fund_type'])
 assert (x['T_date'],x['confirm_date'],x['query_date'])==(r['expected_T'],r['expected_confirm_date'],r['expected_query_date']), (r,x)
print(f'样例通过: {len(rows)}')
batch=list(csv.DictReader((root/'batch_test.csv').open(encoding='utf-8-sig')))
t=time.perf_counter(); [e.calculate(r['submit_time'],r['fund_type']) for r in batch]; elapsed=time.perf_counter()-t
print(f'批量{len(batch)}条: {elapsed:.3f}s')
assert elapsed<5
