"""Read-only contact audit of the committed sample CSVs; never probes contacts."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
EXPORTS = ROOT / "data" / "exports"
REPORT = ROOT / "evaluation" / "reports" / "contact-channel-audit.json"


def read_csv(name: str) -> tuple[list[str], list[dict[str, str]]]:
    with (EXPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def value(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def phone_key(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def license_key(license_number: str) -> str:
    return license_number.strip().upper()


def rate(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 2) if denominator else None


def audit() -> dict:
    property_headers, properties = read_csv("export_properties.csv")
    agent_headers, agents = read_csv("export_agents.csv")
    by_license: dict[str, list[dict[str, str]]] = defaultdict(list)
    for agent in agents:
        if value(agent, "license"):
            by_license[license_key(value(agent, "license"))].append(agent)

    channels = {
        "whatsapp": r"whats\s*app|wa\.me/|api\.whatsapp\.com|whatsapp:",
        "telegram": r"telegram|(?:https?://)?t\.me/|tg://",
        "email": r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        "wechat": r"wechat|weixin|微信",
        "sms_explicit": r"\bsms\b|sms:",
    }

    def occurrences(rows: list[dict[str, str]]) -> dict[str, int]:
        return {
            name: sum(
                bool(re.search(pattern, " ".join(str(v or "") for v in row.values()), re.I))
                for row in rows
            )
            for name, pattern in channels.items()
        }

    groups = []
    for label in ["ALL", "RENT", "SALE"]:
        rows = properties if label == "ALL" else [r for r in properties if r["listing_type"] == label]
        direct = [r for r in rows if value(r, "agent_phone")]
        recovered, mismatches, ambiguous = [], [], []
        for row in rows:
            candidates = by_license.get(license_key(value(row, "agent_license")), [])
            phones = {phone_key(value(a, "phone")) for a in candidates if value(a, "phone")}
            if len(phones) > 1:
                ambiguous.append(row["listing_id"])
            elif len(phones) == 1:
                if not value(row, "agent_phone"):
                    recovered.append(row["listing_id"])
                elif phone_key(value(row, "agent_phone")) not in phones:
                    mismatches.append(row["listing_id"])
        groups.append({
            "scope": label,
            "listings": len(rows),
            "direct_phone_listings": len(direct),
            "direct_phone_pct": rate(len(direct), len(rows)),
            "missing_direct_phone": len(rows) - len(direct),
            "distinct_direct_phones": len({phone_key(value(r, "agent_phone")) for r in direct}),
            "phone_matching_sg_mobile_pattern": sum(bool(re.fullmatch(r"65[89]\d{7}", phone_key(value(r, "agent_phone")))) for r in direct),
            "phone_recoverable_by_unique_license": len(recovered),
            "direct_or_license_phone_listings": len(direct) + len(recovered),
            "direct_or_license_phone_pct": rate(len(direct) + len(recovered), len(rows)),
            "recovered_listing_ids": recovered,
            "phone_conflict_listing_ids": mismatches,
            "ambiguous_license_listing_ids": ambiguous,
            "listing_url_present": sum(bool(value(r, "url")) for r in rows),
            "channel_keyword_occurrence_rows": occurrences(rows),
        })

    wechat_evidence = [
        {"listing_id": r["listing_id"], "listing_type": r["listing_type"],
         "field": "title", "channel": "wechat", "evidence_kind": "explicit_title_text"}
        for r in properties if re.search(channels["wechat"], value(r, "title"), re.I)
    ]
    source_names = ["export_properties.csv", "export_agents.csv", "export_properties_rent.csv", "export_properties_sale.csv"]
    hashes = {name: hashlib.sha256((EXPORTS / name).read_bytes()).hexdigest() for name in source_names}
    consistency = {}
    for listing_type, filename in [("RENT", "export_properties_rent.csv"), ("SALE", "export_properties_sale.csv")]:
        _, separated = read_csv(filename)
        from_all = {r["listing_id"]: r for r in properties if r["listing_type"] == listing_type}
        consistency[listing_type] = {
            "same_rows": from_all == {r["listing_id"]: r for r in separated},
            "unique_listing_ids": len(from_all),
            "separated_rows": len(separated),
        }
    marker_path = ROOT / "data" / ".runtime" / "sample-data-bundle.json"
    installed_source_match = None
    if marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
        installed_source_match = {
            name: hashes[name] == expected
            for name, expected in marker.get("source_sha256", {}).items() if name in hashes
        }

    return {
        "audited_at": datetime.now(ZoneInfo("Asia/Singapore")).isoformat(),
        "source": "local committed historical CSVs used by sample importer; live PostgreSQL was not running",
        "population": "593 historical sample listings, not the full 2026-10-09 snapshot or Singapore market",
        "sources_sha256": hashes,
        "installation_source_hash_matches": installed_source_match,
        "rent_sale_export_consistency": consistency,
        "property_headers": property_headers,
        "agent_headers": agent_headers,
        "groups": groups,
        "agents": {
            "rows": len(agents),
            "unique_agent_ids": len({value(a, "agent_id") for a in agents}),
            "phone_present": sum(bool(value(a, "phone")) for a in agents),
            "distinct_phones": len({phone_key(value(a, "phone")) for a in agents if value(a, "phone")}),
            "phone_matching_sg_mobile_pattern": sum(bool(re.fullmatch(r"65[89]\d{7}", phone_key(value(a, "phone")))) for a in agents),
            "propertyguru_relative_profile_url": sum(value(a, "profile_url").startswith("/agent/") for a in agents),
            "channel_keyword_occurrence_rows": occurrences(agents),
        },
        "wechat_evidence": wechat_evidence,
        "actual_whatsapp_activation_rate": None,
        "contact_activity_verified": False,
        "limitations": [
            "No WhatsApp activation or delivery probes were performed.",
            "A phone number is not evidence of WhatsApp, SMS availability, opt-in, or current agent identity.",
            "Zero explicit WhatsApp references means missing channel evidence, not zero real WhatsApp users.",
            "The agents export contains only saved agent records, not every distinct agent in the listing population.",
            "License association is a read-only recovery calculation; runtime lookup and source CSVs were not changed.",
        ],
    }


if __name__ == "__main__":
    report = audit()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"groups": report["groups"], "agents": report["agents"], "actual_whatsapp_activation_rate": None}, ensure_ascii=False, indent=2))
