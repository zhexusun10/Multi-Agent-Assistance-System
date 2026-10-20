"""Actual Docker Chroma/BM25/RRF ablation; no test question is used as an indexed document."""
from pathlib import Path
import json
import time
from retrieval.hybrid import search

root=Path(__file__).resolve().parents[1]
cases=json.loads((root/'evaluation/rag-corpus.json').read_text(encoding='utf-8'))
rows=[]
for query,doc,heading in cases:
    started=time.perf_counter();r=search([query]);rankings=r['channel_rankings']
    row={'query':query,'expected_document':doc,'expected_heading':heading,'latency_ms':round((time.perf_counter()-started)*1000),'channels':{},'degraded':r['degraded']}
    # Stable chunk IDs let the oracle inspect headings independently of ranking scores.
    from retrieval.index import MANIFEST
    corpus=json.loads(MANIFEST.read_text(encoding='utf-8'));lookup={c['id']:c for c in corpus['chunks']}
    for channel,ids in rankings.items():
        rank=next((i+1 for i,key in enumerate(ids) if lookup[key]['document']['id']==doc and lookup[key]['heading']==heading),None)
        row['channels'][channel]={'section_rank':rank,'section_recall_at_5':bool(rank and rank<=5),'document_recall_at_5':any(lookup[key]['document']['id']==doc for key in ids[:5]),'mrr':1/rank if rank else 0}
    rows.append(row)
metrics={channel:{'section_recall_at_5':sum(r['channels'][channel]['section_recall_at_5'] for r in rows)/len(rows),'document_recall_at_5':sum(r['channels'][channel]['document_recall_at_5'] for r in rows)/len(rows),'mrr':sum(r['channels'][channel]['mrr'] for r in rows)/len(rows)} for channel in ['vector','bm25','fused']}
report={'cases':len(rows),'queries':'original only, no model rewrite or reranking (retrieval ablation)','metrics':metrics,'rows':rows}
out=root/'evaluation/reports/rag-retrieval.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'cases':len(rows),'metrics':metrics,'misses':[r['query'] for r in rows if not r['channels']['fused']['section_recall_at_5']]},ensure_ascii=False))
assert all(not r['degraded'] for r in rows)
assert metrics['fused']['section_recall_at_5']>=.90
