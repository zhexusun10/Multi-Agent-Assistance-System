from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import json
import os
import threading
from .embeddings import EMBED, MODEL, MODEL_ID
from .chroma_store import client
from .hybrid import search
import chromadb
from chromadb.config import Settings
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
INTENT_COLLECTION='estate_intents_'+(os.getenv('RETRIEVAL_NAMESPACE','')+'_' if os.getenv('RETRIEVAL_NAMESPACE') else '')+MODEL_ID

def templates():
    return json.loads((ROOT / 'data/.runtime/intent-templates.json').read_text(encoding='utf-8'))

def intent_collection():
    return client().get_collection(INTENT_COLLECTION,embedding_function=EMBED)

def index_intents():
    data = templates()
    collection = client().get_or_create_collection(INTENT_COLLECTION, embedding_function=EMBED,metadata={'hnsw:space':'cosine','embedding_model':MODEL})
    rows = [(f'{category}:{i}', category, text) for category, texts in data['templates'].items() for i, text in enumerate(texts)]
    collection.upsert(ids=[r[0] for r in rows], documents=[r[2] for r in rows], metadatas=[{'intent':r[1], 'template_version':data['version'], 'embedding_model':MODEL} for r in rows])
    # Prune old templates only after the new version has been completely written.
    old = collection.get(include=['metadatas'])
    stale = [id for id, meta in zip(old['ids'],old['metadatas']) if meta.get('template_version') != data['version'] or id not in {r[0] for r in rows}]
    if stale: collection.delete(ids=stale)
    return {'status':'ok','templates':collection.count(),'template_version':data['version'],'embedding_source':'chroma/'+MODEL}

@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(client().heartbeat)
    yield

app = FastAPI(title='Real Estate Retrieval', lifespan=lifespan)

class IntentQuery(BaseModel):
    query: str = Field(min_length=1,max_length=8000)
    template_version: str

def match_intent(body):
    collection=intent_collection()
    if body.template_version != templates()['version']:
        raise HTTPException(409,'INTENT_TEMPLATE_VERSION_MISMATCH')
    result=collection.query(query_texts=[body.query],n_results=1,where={'template_version':body.template_version},include=['metadatas','distances'])
    if not result['ids'][0]: return {'intent':'other','confidence':0,'reasoning':'No indexed templates'}
    return {'intent':result['metadatas'][0][0]['intent'],'confidence':max(0,min(1,1-result['distances'][0][0])),'reasoning':'Chroma cosine nearest template','source':'chroma/'+MODEL}

@app.post('/intent-match')
async def intent_match(body:IntentQuery):
    return await asyncio.to_thread(match_intent,body)

class KnowledgeQuery(BaseModel):
    queries: list[str] = Field(min_length=1,max_length=4)
    top_k: int = Field(default=5,ge=1,le=20)

@app.post('/search')
async def knowledge_search(body:KnowledgeQuery):
    if any(not q.strip() or len(q)>8000 for q in body.queries):
        raise HTTPException(422,'Invalid query')
    try:
        return await asyncio.to_thread(search,list(dict.fromkeys(body.queries)),body.top_k)
    except Exception:
        raise HTTPException(503,'Knowledge index unavailable; rebuild or inspect service logs')

@app.get('/health')
async def health():
    try:
        await asyncio.to_thread(client().heartbeat)
        count=await asyncio.to_thread(intent_collection().count)
        from .index import MANIFEST
        corpus=json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {}
        return {'status':'ok','intent_templates':count,'knowledge_documents':len(corpus.get('documents',[])), 'knowledge_chunks':len(corpus.get('chunks',[])), 'knowledge_generation':corpus.get('generation'),'embedding_model':MODEL,'chroma_server':True}
    except Exception:
        raise HTTPException(503,'Retrieval index unavailable')

if __name__=='__main__':
    print(json.dumps(index_intents(),ensure_ascii=False))
