"""One versioned multilingual vector space for both stored documents and queries."""
from pathlib import Path
from fastembed import TextEmbedding
import hashlib
import math
import threading
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
MODEL_ID=hashlib.sha256(MODEL.encode()).hexdigest()[:12]
_model=None
_lock=threading.RLock()

class Embeddings:
    def __call__(self,input):
        global _model
        with _lock:
            if _model is None:
                _model=TextEmbedding(model_name=MODEL,cache_dir=str(ROOT/'data/.runtime/models'),threads=2,providers=['CPUExecutionProvider'])
            rows=[]
            for vector in _model.embed(input,batch_size=16):
                values=vector.tolist();norm=math.sqrt(sum(x*x for x in values))
                if not math.isfinite(norm) or norm<=0:raise ValueError('Invalid embedding norm')
                rows.append(np.asarray([x/norm for x in values],dtype=np.float32))
            return rows

EMBED=Embeddings()
