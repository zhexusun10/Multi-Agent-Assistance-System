import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
from data.service.app import create_app

os.environ['NEO4J_URI'] = os.getenv('SAMPLE_NEO4J_URI','bolt://127.0.0.1:7688')
os.environ['NEO4J_AUTH'] = 'none'
os.environ.pop('PROPERTYGURU_READ_DATABASE_URL', None)
info = json.loads((ROOT/'data/.runtime/sample-data-bundle.json').read_text(encoding='utf-8'))
app = create_app(database_url=os.getenv('SAMPLE_PROPERTYGURU_DATABASE_URL','postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample'), dataset_info=info)
