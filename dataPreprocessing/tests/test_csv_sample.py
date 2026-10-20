import csv
import pytest
from sqlalchemy import create_engine
from dataPreprocessing.csv_sample import read_listings, check_sql, check_graph, marker
from dataPreprocessing.storage.models import Base, Property


def test_csv_unknowns_and_identity(tmp_path):
    path = tmp_path / "rent.csv"
    path.write_text(
        "listing_id,listing_type,price,bedrooms,postal_code,latitude\n1,RENT,2500,-1,012345,999\n"
    )
    records, warnings = read_listings(path)
    assert records[0]["price"] == 2500
    assert records[0]["bedrooms"] is None and records[0]["latitude"] is None
    assert records[0]["postal_code"] == "012345" and len(warnings) == 2
    path.write_text(
        "listing_id,listing_type,price,bedrooms\n1,RENT,2500,2\n1,RENT,2600,2\n"
    )
    with pytest.raises(ValueError, match="Conflicting"):
        read_listings(path)


def test_sql_guard():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        assert not check_sql(conn, "hash", 1)
        Base.metadata.create_all(conn)
        conn.execute(
            Property.__table__.insert(), {"listing_id": 1, "listing_type": "RENT"}
        )
        with pytest.raises(ValueError, match="unmarked"):
            check_sql(conn, "hash", 1)
        marker.create(conn)
        conn.execute(marker.insert(), {"id": 1, "source_hash": "hash"})
        assert check_sql(conn, "hash", 1)
        with pytest.raises(ValueError, match="differs"):
            check_sql(conn, "other", 1)
        with pytest.raises(ValueError, match="count"):
            check_sql(conn, "hash", 2)
    engine.dispose()


def test_graph_guard():
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, query, **params):
            class Result:
                def single(self):
                    return {"count": 10} if "count(n)" in query else None

            return Result()

    class Driver:
        def session(self, **kwargs):
            return Session()

    with pytest.raises(ValueError, match="unmarked"):
        check_graph(Driver(), "neo4j", "hash")
