from pathlib import Path
from recommendation.config import RecommendationConfig
from recommendation.agent.controller import LiveController

def test_full_dataset_budget_changes_only_wall_time():
    root=Path(__file__).resolve().parents[1]
    cfg=RecommendationConfig.from_file(root/'config/recommendation-full.json')
    base=RecommendationConfig.default()
    assert cfg.controller.timeout_seconds==300
    # Only the wall-clock budget differs from the committed settings: turns, tool calls, repairs
    # and every scoring/selection parameter are unchanged (same selection_hash).
    expected=base.to_data();expected['controller']['timeout_seconds']=300
    assert cfg.to_data()==expected
    assert cfg.selection_hash==base.selection_hash
    assert LiveController(timeout=cfg.controller.timeout_seconds).timeout==300
