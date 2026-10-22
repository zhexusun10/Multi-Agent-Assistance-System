from pathlib import Path
from recommendation.config import RecommendationConfig,ScoringPolicy
from recommendation.agent.controller import LiveController

def test_full_dataset_budget_changes_only_wall_time():
    root=Path(__file__).resolve().parents[1]
    cfg=RecommendationConfig.from_file(root/'config/recommendation-full.json')
    assert cfg.scoring.model_dump()==ScoringPolicy().model_dump()
    assert cfg.limits.timeout_seconds==300
    assert cfg.limits.model_turns==8 and cfg.limits.tool_calls==20 and cfg.limits.repairs==2
    assert LiveController(timeout=cfg.limits.timeout_seconds).timeout==300
