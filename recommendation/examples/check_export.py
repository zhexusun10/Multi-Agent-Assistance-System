"""Read-only full-export check. Explicit offline mode; no live database claims."""

import csv, json, tempfile
from pathlib import Path
from ..repositories.connections import ROOT, repositories
from ..repositories.listing_repository import normalize
from ..service import RecommendationService
from ..state.store import StateStore
from ..agent.controller import ScriptedController
from recommendation.contracts.request import (
    RecommendationRequest,
)


def main():
    listing, geo = repositories("fixture")
    with (ROOT / "data/exports/export_properties_rent.csv").open() as f:
        listing.rows = [
            normalize(row, "read-only-export:2026-10-09") for row in csv.DictReader(f)
        ]
    with tempfile.TemporaryDirectory() as tmp:
        service = RecommendationService(
            listing, geo, StateStore(Path(tmp) / "state.sqlite"), ScriptedController()
        )
        try:
            request = RecommendationRequest(
                request_id="export-check",
                conversation_id="offline-export",
                requirements_version=1,
                requirements=json.loads(
                    (Path(__file__).parent / "requirements.json").read_text()
                ),
            )
            response = service.recommend(request)
            print(
                json.dumps(
                    {
                        "mode": "explicit offline export / scripted",
                        "status": response.status,
                        "source_count": response.state.source_count,
                        "assessed": response.state.assessed_count,
                        "returned": len(response.recommendations),
                        "gate": service.store.trace("offline-export")["steps"][-1].get(
                            "passed"
                        ),
                        "normalization_warning_records": sum(
                            bool(r.normalization_warnings) for r in listing.rows
                        ),
                        "error": (
                            response.error.model_dump() if response.error else None
                        ),
                    },
                    indent=2,
                )
            )
            if response.status == "error":
                raise SystemExit(1)
        finally:
            service.close()


if __name__ == "__main__":
    main()
