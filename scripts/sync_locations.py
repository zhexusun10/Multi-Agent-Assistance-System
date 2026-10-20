"""Fetch the official public LTA MRT exit snapshot; no model-generated coordinates."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
DATASET = 'd_b39d3a0871985372d7e1637193335da5'
SOURCE = f'https://data.gov.sg/datasets/{DATASET}/view'
with httpx.Client(timeout=40, follow_redirects=True) as client:
    response = client.get(f'https://api-open.data.gov.sg/v1/public/api/datasets/{DATASET}/poll-download')
    response.raise_for_status()
    body = response.json()
    if body.get('code') != 0:
        raise RuntimeError('Official download not ready; retry later')
    response = client.get(body['data']['url'])
    response.raise_for_status()
    raw = response.content
if len(raw) > 5_000_000:
    raise RuntimeError('Unexpected dataset size')
geo = json.loads(raw)
groups = defaultdict(list)
for feature in geo['features']:
    properties = feature['properties']
    name = properties.get('STATION_NA')
    if not name or feature['geometry']['type'] != 'Point':
        continue
    lon, lat = feature['geometry']['coordinates'][:2]
    if not (1.1 <= lat <= 1.5 and 103.5 <= lon <= 104.2):
        raise RuntimeError('Invalid Singapore coordinate')
    groups[name].append({'exit_code':properties.get('EXIT_CODE'), 'object_id':properties.get('OBJECTID'), 'latitude':lat, 'longitude':lon})
if len(groups) < 100 or 'CLEMENTI MRT STATION' not in groups:
    raise RuntimeError('Station coverage is unexpectedly incomplete')
raw_path = ROOT/'data/datasets/lta_mrt_exits.geojson'
raw_path.write_bytes(raw)
catalog = {'version':'lta-mrt-exits-'+hashlib.sha256(raw).hexdigest()[:12]+'-rail-v2', 'source_url':SOURCE,
           'dataset_id':DATASET, 'raw_sha256':hashlib.sha256(raw).hexdigest(),
           'license_url':'https://data.gov.sg/open-data-licence', 'retrieved_at':datetime.now(timezone.utc).isoformat(),
           'measurement':'nearest_recorded_station_exit_straight_line_not_route',
           'stations':[{'place_id':'LTA:'+('LRT' if name.endswith(' LRT STATION') else 'MRT')+':'+name.removesuffix(' MRT STATION').removesuffix(' LRT STATION').replace(' ','_'),
                        'name':name, 'reference_points':points} for name,points in sorted(groups.items())]}
if len({s['place_id'] for s in catalog['stations']}) != len(catalog['stations']):
    raise RuntimeError('Duplicate station IDs; do not publish the catalog')
path = ROOT/'knowledge/locations/lta-stations.json'
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'stations':len(groups),'exits':sum(map(len,groups.values())),'sha256':catalog['raw_sha256'],'source_url':SOURCE}))

# URA planning areas are regions, not invented point proxies or postal districts.
area_id='d_4765db0e87b9c86336792efe8a1f7a66'
with httpx.Client(timeout=50,follow_redirects=True) as client:
    response=client.get(f'https://api-open.data.gov.sg/v1/public/api/datasets/{area_id}/poll-download')
    response.raise_for_status()
    body=response.json()
    if body.get('code')!=0:
        raise RuntimeError('Planning-area download not ready')
    response=client.get(body['data']['url'])
    response.raise_for_status()
    area_raw=response.content
area_geo=json.loads(area_raw)
if len(area_raw)>10_000_000 or len(area_geo['features'])!=55:
    raise RuntimeError('Planning-area coverage changed; review the new snapshot before publishing')
(ROOT/'data/datasets/ura_planning_areas.geojson').write_bytes(area_raw)
areas=[{'place_id':'URA:AREA:'+f['properties']['PLN_AREA_N'].replace(' ','_'),'name':f['properties']['PLN_AREA_N'],'region':f['properties']['REGION_N'],'code':f['properties']['PLN_AREA_C'],'geometry_index':i} for i,f in enumerate(area_geo['features'])]
area_info={'version':'ura-mp2019-'+hashlib.sha256(area_raw).hexdigest()[:12],'source_url':f'https://data.gov.sg/datasets/{area_id}/view','license_url':'https://data.gov.sg/open-data-licence','raw_sha256':hashlib.sha256(area_raw).hexdigest(),'snapshot_period':'Master Plan 2019; metadata must be reviewed when refreshing','retrieved_at':datetime.now(timezone.utc).isoformat(),'areas':areas}
(ROOT/'knowledge/locations/ura-areas.json').write_text(json.dumps(area_info,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'planning_areas':len(areas),'regions':len(set(a['region'] for a in areas)),'sha256':area_info['raw_sha256']}))
