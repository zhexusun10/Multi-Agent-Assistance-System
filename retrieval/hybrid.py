from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import lru_cache
import json
import math
from rank_bm25 import BM25Okapi
from .index import MANIFEST
from .chroma_store import client
from .embeddings import EMBED, MODEL
from .topics import tokenize

RRF_K = 60  # Project extension: EchoMind has no BM25/RRF implementation.


def rrf(rankings, k=RRF_K):
    scores = {}
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] = scores.get(key,0)+1/(k+rank)
    return sorted(scores, key=lambda key:(-scores[key],key)), scores


@lru_cache(maxsize=2)
def snapshot(raw):
    corpus=json.loads(raw)
    if corpus['embedding_model'] != MODEL:
        raise ValueError('KNOWLEDGE_EMBEDDING_MODEL_MISMATCH')
    rows=corpus['chunks']
    return corpus, BM25Okapi([tokenize(r['text']) for r in rows])


def search(queries,top_k=5):
    corpus,bm25=snapshot(MANIFEST.read_text(encoding='utf-8'))
    rows=corpus['chunks']; by_id={r['id']:r for r in rows}
    active={r['id'] for r in rows if r['document']['checked_at']<=date.today().isoformat()<r['document']['review_after']}
    if not active:
        return {'status':'empty','chunks':[],'reason':'All source reviews expired','generation':corpus['generation']}
    recall_k=min(len(rows),max(top_k,5))
    errors=[];vector_ranks=[];sparse_ranks=[];similarities={}
    def vector():
        collection=client().get_collection(corpus['collection'],embedding_function=EMBED)
        if collection.metadata.get('generation')!=corpus['generation']:
            raise ValueError('KNOWLEDGE_GENERATION_MISMATCH')
        return collection.query(query_texts=queries,n_results=recall_k,include=['distances'])
    def sparse():
        result=[]
        for query in queries:
            scores=bm25.get_scores(tokenize(query))
            result.append([rows[i]['id'] for i in sorted(range(len(rows)),key=lambda i:(-scores[i],rows[i]['id'])) if scores[i]>0 and rows[i]['id'] in active][:recall_k])
        return result
    # A channel outage is returned explicitly; the surviving channel can still answer with sources.
    with ThreadPoolExecutor(max_workers=2) as executor:
        vf=executor.submit(vector); sf=executor.submit(sparse)
        try:
            result=vf.result()
            for ids,distances in zip(result['ids'],result['distances']):
                ranking=[]
                for key,distance in zip(ids,distances):
                    similarity=1-distance
                    if key in active and math.isfinite(similarity) and similarity>=0.22:
                        ranking.append(key); similarities[key]=max(similarities.get(key,0),similarity)
                vector_ranks.append(ranking)
        except Exception:
            errors.append('vector_unavailable')
        try: sparse_ranks=sf.result()
        except Exception: errors.append('bm25_unavailable')
    if len(errors)==2:
        raise RuntimeError('ALL_RETRIEVAL_CHANNELS_UNAVAILABLE')
    # Deduplicate each channel over rewrites first: more rewrites never give one channel extra weight.
    vector_ids,_=rrf(vector_ranks); sparse_ids,_=rrf(sparse_ranks)
    fused,scores=rrf([vector_ids,sparse_ids])
    candidates=[]
    for key in fused[:min(20,top_k*3)]:
        row=by_id[key];doc=row['document']
        candidates.append({'chunk_id':key,'text':row['text'],'heading':row['heading'],'document_id':doc['id'],'title':doc['title'],
            'scope':doc['scope'],'revision':doc['revision'],'checked_at':doc['checked_at'],'review_after':doc['review_after'],
            'sources':doc['sources'],'source_ref':doc['source_ref'],'rrf_score':scores[key],
            'vector_rank':vector_ids.index(key)+1 if key in vector_ids else None,
            'bm25_rank':sparse_ids.index(key)+1 if key in sparse_ids else None,'cosine_similarity':similarities.get(key)})
    return {'status':'ok' if candidates else 'empty','chunks':candidates,'generation':corpus['generation'],
        'embedding_model':MODEL,'retrieval':'chroma_bm25_rrf','rrf_k':RRF_K,'recall_k':recall_k,
        'vector_enabled':'vector_unavailable' not in errors,'bm25_enabled':'bm25_unavailable' not in errors,
        'degraded':errors,'channel_rankings':{'vector':vector_ids,'bm25':sparse_ids,'fused':fused}}
