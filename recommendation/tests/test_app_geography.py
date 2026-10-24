import json
from pathlib import Path
import pytest

from recommendation.app_geography import ApplicationGeographicRepository, neighbourhood_catalog
from recommendation.contracts.requirements import Criterion
from recommendation.contracts.errors import RecommendationError
from recommendation.repositories.listing_repository import normalize
from recommendation.repositories.geographic_repository import FixtureGeographicRepository


def criterion(destination, name=None, metric='straight_line'):
    return Criterion(criterion_id='geo', field='distance', operator='lte', value=3000, unit='m', strength='soft',
                     destination_id=destination, destination_name=name, distance_type=metric)


def listing(lat=1.315, lon=103.765, identifier='1'):
    return normalize({'listing_id': identifier, 'listing_type': 'RENT', 'latitude': lat, 'longitude': lon}, 'test')


@pytest.mark.parametrize('destination', ['URA:AREA:CLEMENTI', 'LTA:MRT:CLEMENTI'])
def test_official_distances_use_the_unmodified_native_measurement(destination):
    native = FixtureGeographicRepository()
    wrapped = ApplicationGeographicRepository(native)
    rows, criteria = [listing(), listing(None, None, '2')], [criterion(destination)]
    before = [c.model_dump() for c in criteria]
    original = native.evidence(rows, criteria)
    current = wrapped.evidence(rows, criteria)
    assert [(e.value, e.method, e.destination_id, e.unknown_reason) for e in current] == [
        (e.value, e.method, e.destination_id, e.unknown_reason) for e in original]
    assert current[1].value is None
    assert [c.model_dump() for c in criteria] == before


def test_existing_osm_fixture_places_keep_their_identity_and_distance():
    native = FixtureGeographicRepository()
    wrapped = ApplicationGeographicRepository(native)
    place = native.catalog().places.places[0]
    c, rows = criterion(place.place_id), [listing()]
    assert wrapped.resolve_criteria([c])['geo'].entity_id == place.place_id
    assert wrapped.evidence(rows, [c])[0].value == native.evidence(rows, [c])[0].value


@pytest.mark.parametrize('name', ['Chinatown', 'Holland Village'])
def test_application_neighbourhoods_remain_neighbourhoods_not_same_named_stations(name):
    wrapped = ApplicationGeographicRepository(FixtureGeographicRepository())
    row = next(r for r in neighbourhood_catalog()[0] if r['name'] == name)
    c = criterion(row['place_id'], row['name'])
    result = wrapped.resolve_criteria([c])['geo']
    assert result.status == 'resolved' and result.entity_id == row['place_id']
    assert result.entity_kind == 'osm_place'
    assert wrapped.evidence([listing(row['latitude'], row['longitude'])], [c], resolutions={'geo': result})[0].value == 0
    wrong = criterion(row['place_id'], 'Clementi MRT Station')
    assert wrapped.resolve_criteria([wrong])['geo'].status == 'ambiguous'


def test_application_catalogue_fingerprint_participates_in_cache_identity_and_invalid_files_fail(tmp_path):
    native = FixtureGeographicRepository()
    wrapped = ApplicationGeographicRepository(native)
    assert wrapped.catalog_version() != native.catalog_version()
    assert wrapped.catalog_version() == wrapped.catalog_version()
    root = Path(__file__).resolve().parents[2]
    for name in ['knowledge/locations/neighbourhoods.json', 'data/datasets/osm_neighbourhoods.json']:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    path = tmp_path / 'data/datasets/osm_neighbourhoods.json'
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(RecommendationError, match='catalogue'):
        neighbourhood_catalog(tmp_path)
