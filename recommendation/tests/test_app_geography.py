import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from recommendation.app_geography import ApplicationGeographicRepository, measure_official
from recommendation.contracts.requirements import Criterion
from recommendation.contracts.errors import RecommendationError
from recommendation.repositories.listing_repository import normalize
from recommendation.repositories.geographic_repository import FixtureGeographicRepository
from recommendation.validation.rules import evaluate

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / 'knowledge/locations/ura-areas.json').read_text(encoding='utf-8'))


def criterion(destination='URA:AREA:CLEMENTI', metric='straight_line', strength='soft'):
    return Criterion(criterion_id='geo', field='distance', operator='lte', value=3000, unit='m', strength=strength,
                     destination_id=destination, distance_type=metric)


def listing(lat=1.315, lon=103.765, id='1'):
    return normalize({'listing_id': id, 'listing_type': 'RENT', 'latitude': lat, 'longitude': lon}, 'test')


class Native(FixtureGeographicRepository):
    def query(self, query, ids):
        return [{'place_id': id, 'name': 'CLEMENTI', 'dataset_version': CATALOG['version'], 'source': 'ura_mp2019'}
                for id in ids if id == 'URA:AREA:CLEMENTI']


def test_osm_repository_is_delegated_without_reinterpreting_its_evidence():
    native = Native()
    calls = []
    c = criterion('fixture-school')
    expected = native.evidence([listing()], [c])
    repo = ApplicationGeographicRepository(native, lambda *_: calls.append('must not measure'))
    actual = repo.evidence([listing()], [c])
    assert not calls and actual[0].value == expected[0].value
    assert actual[0].snapshot_hash == expected[0].snapshot_hash
    assert actual[0].method == expected[0].method and actual[0].source == expected[0].source


def test_real_catalog_area_inside_zero_and_outside_distance_feed_the_existing_hard_filter():
    repo = ApplicationGeographicRepository(Native())
    rows = [listing(), listing(1.4, 103.95, id='2'), listing(None, None, id='3')]
    c = criterion(strength='hard')
    first = repo.evidence(rows, [c])
    assert first[0].value == 0 and first[1].value > 10000
    assert first[2].value is None and 'listing coordinates' in first[2].unknown_reason
    assert [evaluate(row, [c], first)[0].outcome for row in rows] == ['pass', 'fail', 'unknown']
    assert first[0].method == 'turf.geodesic.distance_to_area_zero_inside'
    assert repo.last_snapshot[0]['official_catalogues'][0]['raw_sha256'] == CATALOG['raw_sha256']
    again = repo.evidence(rows, [c])
    assert [e.snapshot_hash for e in again] == [e.snapshot_hash for e in first]


def test_official_station_uses_nearest_recorded_exit_not_the_average_station_point():
    data = json.loads((ROOT / 'knowledge/locations/lta-stations.json').read_text(encoding='utf-8'))
    station = next(s for s in data['stations'] if s['place_id'] == 'LTA:MRT:CLEMENTI')
    point = station['reference_points'][0]
    measured = measure_official([listing(point['latitude'], point['longitude'])], [criterion(station['place_id'])])
    assert measured['measurements'][0]['value'] == 0
    assert measured['measurements'][0]['method'] == 'haversine.nearest_recorded_exit'
    assert measured['catalogues'][0]['place_id'] == 'LTA:MRT:CLEMENTI'


def test_unknown_graph_destination_and_route_modes_never_become_zero_or_a_station_substitution():
    calls = []
    repo = ApplicationGeographicRepository(Native(), lambda *_: calls.append('unexpected'))
    unknown = repo.evidence([listing()], [criterion('URA:AREA:UNDEFINED')])[0]
    assert unknown.value is None and unknown.mapping_status == 'missing' and not calls
    route = ApplicationGeographicRepository(Native()).evidence([listing()], [criterion(metric='walking')])[0]
    assert route.value is None and 'route evidence unavailable' in route.unknown_reason


def test_graph_catalogue_mismatch_and_extra_or_duplicate_observations_are_rejected():
    row, c = listing(), criterion()
    measured = measure_official([row], [c])
    measured['measurements'][0]['catalogue']['version'] = 'different-source'
    with pytest.raises(RecommendationError):
        ApplicationGeographicRepository(Native(), lambda *_: measured).evidence([row], [c])
    measured = measure_official([row], [c])
    measured['measurements'].append(measured['measurements'][0])
    with pytest.raises(RecommendationError):
        ApplicationGeographicRepository(Native(), lambda *_: measured).evidence([row], [c])
