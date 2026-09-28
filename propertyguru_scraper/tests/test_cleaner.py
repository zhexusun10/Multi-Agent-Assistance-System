import pytest
from datetime import datetime, timezone
from cleaner import PropertyCleaner

def test_parse_numeric():
    assert PropertyCleaner.parse_numeric("1,480,000") == 1480000.0
    assert PropertyCleaner.parse_numeric("S$ 1,856.96 psf") == 1856.96
    assert PropertyCleaner.parse_numeric(None) is None
    assert PropertyCleaner.parse_numeric("N/A") is None

def test_parse_mrt():
    # Meters case
    mrt1 = {"nearbyText": "5 min (410 m) from CC12 Bartley MRT Station"}
    stn, dist, mins = PropertyCleaner.parse_mrt(mrt1)
    assert stn == "CC12 Bartley MRT Station"
    assert dist == 410
    assert mins == 5

    # Kilometers case
    mrt2 = {"nearbyText": "17 min (1.39 km) from TE5 Lentor MRT Station"}
    stn, dist, mins = PropertyCleaner.parse_mrt(mrt2)
    assert stn == "TE5 Lentor MRT Station"
    assert dist == 1390
    assert mins == 17

def test_parse_features_landed():
    features = [
        [{"text": "4"}, {"text": "4"}],
        {"text": "2,800 sqft (floor), 2,524 sqft (land)"},
        {"text": "Terraced House"},
        {"text": "Freehold"},
        {"text": "Built: 1999"}
    ]
    res = PropertyCleaner.parse_features(features)
    assert res["bedrooms"] == 4
    assert res["bathrooms"] == 4
    assert res["floor_area_sqft"] == 2800.0
    assert res["land_area_sqft"] == 2524.0
    assert res["property_type"] == "Terraced House"
    assert res["tenure"] == "Freehold"
    assert res["tenure_category"] == "Freehold"
    assert res["build_year"] == 1999

def test_clean_listing_full():
    sample_raw = {
        "listingData": {
            "id": 500232338,
            "statusCode": "ACT",
            "typeCode": "SALE",
            "localizedTitle": "Bartley Residences",
            "price": {
                "value": 1480000,
                "type": {"code": "NEG", "text": "Negotiable"},
                "currency": "SGD"
            },
            "psfText": "S$ 1,856.96 psf",
            "floorArea": 797,
            "bedrooms": 2,
            "bathrooms": 2,
            "property": {
                "subTypeText": "Condominium"
            },
            "listingFeatures": [
                [{"text": "2"}, {"text": "2"}],
                {"text": "797 sqft"},
                {"text": "Condominium"},
                {"text": "99-year Leasehold"},
                {"text": "Built: 2015"}
            ],
            "additionalData": {
                "tenure": "L99",
                "districtCode": "D19",
                "districtText": "Hougang / Punggol / Sengkang",
                "regionCode": "G",
                "regionText": "Serangoon / Thomson (D19-20)"
            },
            "mrt": {
                "nearbyText": "5 min (410 m) from CC12 Bartley MRT Station"
            },
            "agent": {
                "id": 434925,
                "name": "Billy Ng Jun Jie",
                "license": "R056266E"
            },
            "agency": {
                "id": 2,
                "name": "ERA REALTY NETWORK PTE LTD"
            },
            "postedOn": {
                "unix": 1788755794
            },
            "url": "https://www.propertyguru.com.sg/listing/500232338",
            "thumbnail": "https://sg1-cdn.pgimgs.com/sample.jpg"
        }
    }

    cleaned = PropertyCleaner.clean_listing(sample_raw)
    assert cleaned is not None
    assert cleaned["listing_id"] == 500232338
    assert cleaned["listing_type"] == "SALE"
    assert cleaned["title"] == "Bartley Residences"
    assert cleaned["price"] == 1480000.0
    assert cleaned["price_type"] == "Negotiable"
    assert cleaned["psf"] == 1856.96
    assert cleaned["bedrooms"] == 2
    assert cleaned["bathrooms"] == 2
    assert cleaned["floor_area_sqft"] == 797.0
    assert cleaned["tenure_category"] == "99-year Leasehold"
    assert cleaned["build_year"] == 2015
    assert cleaned["district_code"] == "D19"
    assert cleaned["nearest_mrt"] == "CC12 Bartley MRT Station"
    assert cleaned["mrt_distance_m"] == 410
    assert cleaned["mrt_walking_mins"] == 5
    assert cleaned["agent_name"] == "Billy Ng Jun Jie"
    assert cleaned["agency_name"] == "ERA REALTY NETWORK PTE LTD"
    assert cleaned["posted_at"] is not None

def test_separated_images_and_detail_enrichment():
    # 1. Test homepage images extraction
    sample_srp = {
        "listingData": {
            "id": 60029397,
            "typeCode": "SALE",
            "thumbnail": "https://sg1-cdn.pgimgs.com/listing/60029397/thumb.jpg",
            "mediaCarousel": {
                "previewMedia": {
                    "images": {
                        "items": [
                            {"src": "https://sg1-cdn.pgimgs.com/preview1.jpg", "caption": "Living room"},
                            {"src": "https://sg1-cdn.pgimgs.com/preview2.jpg", "caption": "Bedroom"}
                        ]
                    }
                }
            }
        }
    }
    cleaned = PropertyCleaner.clean_listing(sample_srp)
    assert cleaned is not None
    assert cleaned["homepage_images"]["thumbnail"] == "https://sg1-cdn.pgimgs.com/listing/60029397/thumb.jpg"
    assert len(cleaned["homepage_images"]["preview_images"]) == 2
    assert cleaned["detail_images"] is None

    # 2. Test detail enrichment (exact address, postal code, detail images)
    sample_detail = {
        "listingDetail": {
            "location": {
                "streetName": "Amber Gardens",
                "address": {
                    "formatted": "30 Amber Gardens",
                    "postalCode": "439964",
                    "streetNumber": "30",
                    "block": "Blk A"
                },
                "point": {
                    "lat": 1.301783,
                    "lon": 103.900426
                }
            }
        },
        "mediaGalleryData": {
            "media": {
                "images": {
                    "items": [
                        {"src": "https://sg1-cdn.pgimgs.com/photo1.jpg", "caption": "Detail Photo 1"},
                        {"src": "https://sg1-cdn.pgimgs.com/photo2.jpg", "caption": "Detail Photo 2"}
                    ]
                },
                "floorPlans": {
                    "items": [
                        {"src": "https://sg1-cdn.pgimgs.com/fp1.jpg", "caption": "Floor Plan 1"}
                    ]
                }
            }
        }
    }

    enriched = PropertyCleaner.enrich_from_detail(cleaned, sample_detail)
    assert enriched["postal_code"] == "439964"
    assert enriched["street_name"] == "Amber Gardens"
    assert enriched["street_number"] == "30"
    assert enriched["block"] == "Blk A"
    assert enriched["latitude"] == 1.301783
    assert enriched["longitude"] == 103.900426
    assert enriched["detail_fetched"] is True
    assert enriched["detail_images"]["photo_count"] == 2
    assert enriched["detail_images"]["floor_plan_count"] == 1

    # 3. Test conversion to separated property_images rows
    img_rows = PropertyCleaner.extract_separated_image_records(enriched)
    assert len(img_rows) == 6 # 1 thumb + 2 preview + 2 photo + 1 floorplan
    
    hp_rows = [r for r in img_rows if r["source_page"] == "HOMEPAGE"]
    dp_rows = [r for r in img_rows if r["source_page"] == "DETAIL_PAGE"]
    assert len(hp_rows) == 3
    assert len(dp_rows) == 3
    assert any(r["image_type"] == "THUMBNAIL" for r in hp_rows)
    assert any(r["image_type"] == "PREVIEW" for r in hp_rows)
    assert any(r["image_type"] == "PHOTO" for r in dp_rows)
    assert any(r["image_type"] == "FLOOR_PLAN" for r in dp_rows)

def test_extract_agent_and_price_history():
    # 1. Agent extraction
    sample_detail = {
        "contactAgentData": {
            "contactAgentCard": {
                "agency": {"name": "ERA REALTY NETWORK PTE LTD"},
                "agentInfoProps": {
                    "agent": {
                        "id": 434925,
                        "name": "Billy Ng Jun Jie",
                        "avatar": "https://sg1-cdn.pgimgs.com/agent/photo.jpg",
                        "mobile": "+6591196230",
                        "description": "<div>CEA: R056266E</div>"
                    },
                    "tenure": {
                        "years": 10,
                        "text": "10 years",
                        "suffix": "with PropertyGuru"
                    }
                }
            }
        },
        "listingDetail": {
            "lister": {
                "metaByType": {
                    "agent": {
                        "id": 434925,
                        "license": "R056266E"
                    }
                }
            }
        }
    }
    
    cleaned = {"listing_id": 500232338}
    enriched = PropertyCleaner.enrich_from_detail(cleaned, sample_detail)
    assert enriched["agent_id"] == 434925
    assert enriched["agent_name"] == "Billy Ng Jun Jie"
    assert enriched["agent_license"] == "R056266E"
    assert enriched["agent_years_with_pg"] == "10 years with PropertyGuru"
    assert enriched["agent_years_count"] == 10
    assert enriched["agency_name"] == "ERA REALTY NETWORK PTE LTD"

    agent_row = PropertyCleaner.extract_agent_record(enriched)
    assert agent_row is not None
    assert agent_row["agent_id"] == 434925
    assert agent_row["years_with_pg"] == "10 years with PropertyGuru"
    assert agent_row["license"] == "R056266E"

    # 2. Project Transactions (Price History) parsing
    sample_html = """
    <script>
    var txData = [
      {"building":"Blk 5A","psf":1586,"price":888000,"level":"04","size":560,"contract_date":"2026-05-29","bedrooms":1,"transaction_type":"sales","district_code":"D19","postcode":"536563","property_type":"Apartment"}
    ];
    </script>
    """
    txs = PropertyCleaner.parse_project_transactions(sample_html, 21131, "Bartley Residences")
    assert len(txs) == 1
    t0 = txs[0]
    assert t0["project_id"] == 21131
    assert t0["project_name"] == "Bartley Residences"
    assert t0["price"] == 888000.0
    assert t0["psf"] == 1586.0
    assert t0["building"] == "Blk 5A"
    assert t0["floor_level"] == "04"
    assert t0["bedrooms"] == 1
    assert t0["transaction_type"] == "sales"
