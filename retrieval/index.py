"""Offline Markdown -> 500 character chunks -> versioned Chroma generation, atomically published."""
from datetime import date
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
import json
import os
import re
import yaml
from .chroma_store import client
from .embeddings import EMBED, MODEL

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'data/.runtime/knowledge-index.json'
CHUNK_SIZE = 500  # EchoMind's sentence/newline packing, no overlapping duplicate sentences.
ALGORITHM = 'markdown-heading-sentence-500-v1'
OFFICIAL = {'www.ura.gov.sg','www.hdb.gov.sg','www.iras.gov.sg','www.cea.gov.sg','www.police.gov.sg','www.judiciary.gov.sg'}


def source_allowed(source, scope):
    u = urlparse(source['url'])
    return (u.scheme == 'https' and u.hostname in OFFICIAL and not u.username and not u.password) or (
        scope == 'system_help' and source['url'].startswith('project://knowledge/rental/'))


def split_sections(body, title):
    heading = title
    paragraphs = []
    for line in body.splitlines():
        if line.startswith('#'):
            if paragraphs:
                yield heading, '\n'.join(paragraphs)
                paragraphs = []
            heading = line.lstrip('# ').strip()
        elif line.strip():
            paragraphs.append(line.strip())
    if paragraphs:
        yield heading, '\n'.join(paragraphs)


def chunks(body, title):
    for heading, text in split_sections(body, title):
        prefix = title+'\n'+heading+'\n'
        capacity = CHUNK_SIZE-len(prefix)
        if capacity < 50:
            raise ValueError('Heading too long')
        buffer = ''
        for sentence in re.split(r'(?<=[。！？!?])|\n', text):
            if not sentence:
                continue
            # Even a pathological long sentence is bounded. No text is dropped.
            for start in range(0, len(sentence), capacity):
                part = sentence[start:start+capacity]
                if len(buffer)+len(part) > capacity:
                    yield heading, prefix+buffer
                    buffer = ''
                buffer += part
        if buffer:
            yield heading, prefix+buffer


def load_corpus(directory=None, today=None):
    today = today or date.today().isoformat()
    rows, documents = [], []
    for path in sorted((directory or ROOT/'knowledge/rental').glob('*.md')):
        raw = path.read_text(encoding='utf-8')
        parts = raw.split('---', 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError(f'Missing metadata: {path.name}')
        meta = yaml.safe_load(parts[1])
        for name in ['id','title','scope','revision','checked_at','review_after','sources']:
            if not meta.get(name):
                raise ValueError(f'Missing {name}: {path.name}')
        if not re.fullmatch(r'[a-z0-9-]+', meta['id']) or meta['id'] in {d['id'] for d in documents}:
            raise ValueError('Invalid/duplicate document identity')
        if not (str(meta['checked_at']) <= today < str(meta['review_after'])):
            raise ValueError(f'Source review expired or future-dated: {path.name}')
        if meta['scope'] not in ['system_help','policy_guidance'] or not all(source_allowed(s,meta['scope']) for s in meta['sources']):
            raise ValueError('Unapproved source')
        doc = {**meta, 'source_ref':f'knowledge/rental/{path.name}', 'content_sha256':sha256(raw.encode()).hexdigest()}
        documents.append(doc)
        for i, (heading, text) in enumerate(chunks(parts[2],meta['title'])):
            rows.append({'id':f'{meta["id"]}:{i}:{sha256(text.encode()).hexdigest()[:12]}', 'text':text, 'heading':heading, 'document':doc})
    if not rows:
        raise ValueError('Empty knowledge corpus')
    generation = sha256(json.dumps([MODEL,ALGORITHM,documents,rows],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'generation':generation,'collection':'estate_knowledge_'+generation[:24], 'embedding_model':MODEL,
        'chunk_size':CHUNK_SIZE,'overlap':0,'algorithm':ALGORITHM,'documents':documents,'chunks':rows}


def index_knowledge():
    corpus = load_corpus()
    collection = client().get_or_create_collection(corpus['collection'],embedding_function=EMBED,
        metadata={'hnsw:space':'cosine','embedding_model':MODEL,'generation':corpus['generation']})
    rows=corpus['chunks']
    # A fully built generation is published only after both stores have the same content IDs.
    existing=set(collection.get(include=[])['ids'])
    for start in range(0,len(rows),32):
        batch=[r for r in rows[start:start+32] if r['id'] not in existing]
        if batch:
            collection.upsert(ids=[r['id'] for r in batch],documents=[r['text'] for r in batch],
                metadatas=[{'document_id':r['document']['id'],'generation':corpus['generation'],'review_after':r['document']['review_after']} for r in batch])
    if set(collection.get(include=[])['ids']) != {r['id'] for r in rows}:
        raise ValueError('Incomplete generation')
    MANIFEST.parent.mkdir(parents=True,exist_ok=True)
    tmp=MANIFEST.with_suffix('.tmp')
    tmp.write_text(json.dumps(corpus,ensure_ascii=False),encoding='utf-8')
    os.replace(tmp,MANIFEST)
    return {'status':'ok','documents':len(corpus['documents']),'chunks':len(rows),'generation':corpus['generation'],'embedding_model':MODEL}


if __name__=='__main__':
    print(json.dumps(index_knowledge(),ensure_ascii=False))
