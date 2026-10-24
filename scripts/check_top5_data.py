"""Run the real local data/ranking path in an isolated process with ZERO LLM calls.

Does not change the running service's controller or configuration.
"""
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from recommendation.app import execute
from recommendation.contracts.request import RecommendationRequest

source, destination = map(Path, sys.argv[1:3])
request = RecommendationRequest.model_validate(json.loads(source.read_text('utf-8')))
with TemporaryDirectory(prefix='top5-local-') as isolated:
    os.environ['RECOMMENDATION_CONTROLLER'] = 'scripted'
    os.environ['RECOMMENDATION_DATA_MODE'] = 'database'
    os.environ['RECOMMENDATION_STATE_PATH'] = str(Path(isolated) / 'state.sqlite')
    result = execute(request, app_view=True)
    assert result['response']['status'] == 'success', result['response']['error']
    assert result['response']['execution']['model_calls'] == 0
    assert len(result['response']['recommendations']) == 5
    assert [x['rank'] for x in result['response']['recommendations']] == [1, 2, 3, 4, 5]
    destination.write_text(json.dumps(result), encoding='utf-8')
print(json.dumps({'status': 'passed', 'ranked': 5, 'native_model_calls': 0}))
