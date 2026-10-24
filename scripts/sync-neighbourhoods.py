"""Refresh reviewed OSM neighbourhood nodes without substituting MRT stations.

This is an explicit data-maintenance command, never run during user intake.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REVIEWED = {2511049756: ('Chinatown', ['牛车水', 'Chinatown Singapore']),
            8777049478: ('Holland Village', ['荷兰村', 'Holland V', 'HollandV'])}


def main():
    url = 'https://api.openstreetmap.org/api/0.6/nodes.json?nodes=' + ','.join(map(str, REVIEWED))
    response = httpx.get(url, timeout=25, follow_redirects=True)
    response.raise_for_status()
    elements = response.json()['elements']
    if {e['id'] for e in elements} != set(REVIEWED):
        raise ValueError('Neighbourhood coverage changed; review before publishing')
    records = []
    for e in elements:
        name, aliases = REVIEWED[e['id']]
        tags = e['tags']
        if (e['type'] != 'node' or tags.get('name') != name
                or tags.get('place') not in ('neighbourhood', 'suburb')
                or not (1.1 < e['lat'] < 1.5 and 103.5 < e['lon'] < 104.2)):
            raise ValueError('Unexpected identity, type or coordinates')
        records.append({'place_id': f"OSM:node:{e['id']}", 'name': name,
                        'name_local': tags.get('name:zh'), 'aliases': aliases,
                        'category': 'Neighbourhood', 'latitude': e['lat'], 'longitude': e['lon'],
                        'source_url': f"https://www.openstreetmap.org/node/{e['id']}",
                        'source_updated_at': e['timestamp'], 'osm_version': e['version'],
                        'osm_place_type': tags['place'], 'coordinate_method': 'osm_neighbourhood_label_point'})
    raw = (json.dumps(records, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    (ROOT / 'data/datasets/osm_neighbourhoods.json').write_bytes(raw)
    catalog = {'version': 'osm-neighbourhoods-' + digest[:12], 'raw_sha256': digest,
               'source_url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
               'license_url': 'https://opendatacommons.org/licenses/odbl/1-0/',
               'attribution': '© OpenStreetMap contributors',
               'measurement': 'neighbourhood_label_point_straight_line_not_boundary_or_station',
               'count': len(records)}
    (ROOT / 'knowledge/locations/neighbourhoods.json').write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', 'neighbourhoods': len(records), 'sha256': digest}))


if __name__ == '__main__':
    main()
