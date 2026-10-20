"""Paired before/after metrics; exclude an explicitly corrected annotation."""
import json
from pathlib import Path
root = Path(__file__).resolve().parent
report = {'excluded_from_before_after':['R058'],'reason':'R058 originally used unapproved NER; data/README.md defines CCR/RCR/OCR. Original reports retain the original corpus hash. Final label requires clarification; exclude this case from paired metrics.'}
for mode in ['rules','model']:
    report[mode] = {}
    for stage in ['before','after']:
        data = json.loads((root/f'reports/intake-{stage}-{mode}.json').read_text(encoding='utf8'))
        rows = [r for r in data['results'] if r['id'] != 'R058']
        tp = sum(r['tp'] for r in rows)
        expected = sum(r['expected'] for r in rows)
        predicted = sum(r['predicted'] for r in rows)
        p = tp/predicted if predicted else 0
        r = tp/expected if expected else 0
        report[mode][stage] = {'cases':len(rows),'exact_pass':sum(r['passed'] for r in rows),'exact_accuracy':sum(r['passed'] for r in rows)/len(rows),'precision':p,'recall':r,'f1':2*p*r/(p+r) if p+r else 0,'exceptions':sum(bool(r.get('exception')) for r in rows),'corpus_sha256':data['corpus_sha256']}
(root/'reports/intake-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(report,ensure_ascii=False,indent=2))
