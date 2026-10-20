"""Import committed historical CSVs into explicitly isolated sample stores."""
import csv
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.sql.sqltypes import Integer, Numeric, Date, DateTime, Boolean
from data.storage.models import Base, Property, Agent, PropertyImage, PriceHistory
from data.graph.sync import create_graph_driver, sync_projection
from data.storage.snapshot import sql_counts

load_dotenv(ROOT / '.env')
VERSION = 'historical-csv-main-0c45bf3'
url = os.getenv('SAMPLE_PROPERTYGURU_DATABASE_URL', 'postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample')
uri = os.getenv('SAMPLE_NEO4J_URI', 'bolt://127.0.0.1:7688')
if not url.split('?', 1)[0].endswith('/propertyguru_sample') or uri != 'bolt://127.0.0.1:7688':
    raise SystemExit('This bootstrap only writes propertyguru_sample and Bolt port 7688; full-snapshot stores are forbidden.')
engine = create_engine(url)
sources = [(Property, 'export_properties.csv'), (Agent, 'export_agents.csv'), (PropertyImage, 'export_images.csv'), (PriceHistory, 'export_price_history.csv')]
hashes = {name: hashlib.sha256((ROOT/'data/exports'/name).read_bytes()).hexdigest() for _, name in sources}

def convert(model, path):
    rows = []
    for raw in csv.DictReader(path.open(encoding='utf-8-sig', newline='')):
        row = {}
        for column in model.__table__.columns:
            if column.name not in raw:
                continue
            value = raw[column.name]
            if value == '':
                row[column.name] = None
            elif isinstance(column.type, DateTime):
                row[column.name] = datetime.fromisoformat(value.replace('Z', '+00:00'))
            elif isinstance(column.type, Date):
                row[column.name] = date.fromisoformat(value)
            elif isinstance(column.type, Integer):
                row[column.name] = int(value)
            elif isinstance(column.type, Numeric):
                row[column.name] = float(value)
            elif isinstance(column.type, Boolean):
                row[column.name] = value.lower() in ('true', '1')
            else:
                row[column.name] = value
        if model is Property:
            # Currency is the repository export's SGD convention, not inferred FX.
            row['currency'] = 'SGD'
            if row.get('bedrooms') is not None and row['bedrooms'] < 0:
                row['bedrooms'] = None  # undocumented negative sentinel cannot prove a count
        rows.append(row)
    return rows

Base.metadata.create_all(engine)
with engine.begin() as conn:
    conn.execute(text('CREATE TABLE IF NOT EXISTS sample_installation_metadata (version TEXT PRIMARY KEY, hashes JSONB NOT NULL)'))
    marker = conn.execute(text('SELECT hashes FROM sample_installation_metadata WHERE version=:v'), {'v': VERSION}).scalar()
    if marker and marker != hashes:
        raise SystemExit('Sample source hash changed. Refusing to mix datasets.')
    if not marker:
        if any(sql_counts(engine, allow_missing=True).values()):
            raise SystemExit('Nonempty sample store without matching import marker; refusing overwrite.')
        for model, name in sources:
            rows = convert(model, ROOT/'data/exports'/name)
            for offset in range(0, len(rows), 500):
                conn.execute(model.__table__.insert(), rows[offset:offset+500])
        conn.execute(text('INSERT INTO sample_installation_metadata(version,hashes) VALUES(:v,CAST(:h AS jsonb))'), {'v':VERSION, 'h':json.dumps(hashes)})
counts = sql_counts(engine)
assert counts == {'properties':593,'agents':162,'images':19097,'price_history':1665}, counts
os.environ['NEO4J_URI'] = uri
os.environ['NEO4J_AUTH'] = 'none'
with create_graph_driver() as driver:
    projection = sync_projection(engine, driver=driver, batch_size=200)
    with driver.session(database='neo4j') as session:
        rent = session.run("MATCH (n:Listing {listing_type:'RENT'}) RETURN count(n) AS count").single()['count']
        assert rent == 475, rent
info = {'dataset_version':VERSION,'date':'historical CSV export; not the 2026-10-09 full snapshot','immutable':True,'expected_counts':counts,'current_availability_verified':False,'scope':'593 historical listings: 475 RENT and 118 SALE; no market-wide completeness','source_sha256':hashes,'verified_at':datetime.now(timezone.utc).isoformat(),'normalizations':['negative bedroom sentinel -> unknown','currency SGD from repository export convention']}
destination = ROOT/'data/.runtime/sample-data-bundle.json'
destination.parent.mkdir(exist_ok=True, parents=True)
destination.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status':'sample_imported','counts':counts,'rental_listings':rent,'projection':projection}, indent=2))
engine.dispose()
