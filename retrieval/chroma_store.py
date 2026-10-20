import os
import threading
from pathlib import Path
from dotenv import load_dotenv
import chromadb
from chromadb.config import Settings

_CLIENT = None
_LOCK = threading.RLock()
load_dotenv(Path(__file__).resolve().parents[1]/'.env',override=False)


def client():
    global _CLIENT
    with _LOCK:
        if _CLIENT is None:
            _CLIENT = chromadb.HttpClient(host=os.getenv('CHROMA_HOST', '127.0.0.1'),
                port=int(os.getenv('CHROMA_PORT', '8002')), settings=Settings(anonymized_telemetry=False))
        return _CLIENT
