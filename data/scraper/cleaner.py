import re
import json
import logging
from datetime import datetime, date, timezone
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

class PropertyCleaner:
    @staticmethod
    def parse_numeric(val: Any) -> Optional[float]:
        """Safely parse numbers from strings or numbers."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        cleaned = re.sub(r"[^\d.]", "", str(val))
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    @staticmethod
    def parse_int(val: Any) -> Optional[int]:
        """Safely parse integer."""
        if val is None:
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        cleaned = re.sub(r"[^\d]", "", str(val))
        try:
            return int(cleaned) if cleaned else None
        except ValueError:
            return None

    @staticmethod
    def parse_price_info(price_dict: Any) -> Tuple[Optional[float], Optional[str], Optional[str]]:
        """Extract price, currency, and price type from price dict or raw value."""
        if not price_dict:
            return None, "SGD", None
            
        if isinstance(price_dict, (int, float)):
            return float(price_dict), "SGD", None
            
        if not isinstance(price_dict, dict):
            return PropertyCleaner.parse_numeric(price_dict), "SGD", None

        val = price_dict.get("value")
        price = PropertyCleaner.parse_numeric(val)
        if price is None:
            price = PropertyCleaner.parse_numeric(price_dict.get("localeStringValue"))
        if price is None:
            price = PropertyCleaner.parse_numeric(price_dict.get("pretty"))
            
        currency = price_dict.get("currency") or "SGD"
        
        p_type = None
        type_obj = price_dict.get("type")
        if isinstance(type_obj, dict):
            p_type = type_obj.get("text") or type_obj.get("code")
        elif isinstance(type_obj, str):
            p_type = type_obj
            
        return price, currency, p_type

    @staticmethod
    def parse_psf(psf_text: Any, price: Optional[float], floor_area: Optional[float]) -> Optional[float]:
        """Extract price per square foot or calculate if possible."""
        if psf_text:
            match = re.search(r"([\d,]+(?:\.\d+)?)", str(psf_text))
            if match:
                psf_val = PropertyCleaner.parse_numeric(match.group(1))
                if psf_val:
                    return round(psf_val, 2)
                    
        if price and floor_area and floor_area > 0:
            return round(price / floor_area, 2)
        return None

    @staticmethod
    def parse_features(features: list) -> Dict[str, Any]:
        """Parse listingFeatures array."""
        res = {
            "floor_area_sqft": None,
            "land_area_sqft": None,
            "property_type": None,
            "tenure": None,
            "tenure_category": "Other",
            "build_year": None,
            "bedrooms": None,
            "bathrooms": None
        }

        if not features or not isinstance(features, list):
            return res

        for item in features:
            if isinstance(item, list):
                bed_val = None
                bath_val = None
                if len(item) >= 1:
                    bed_val = item[0].get("text") if isinstance(item[0], dict) else item[0]
                if len(item) >= 2:
                    bath_val = item[1].get("text") if isinstance(item[1], dict) else item[1]
                if bed_val:
                    res["bedrooms"] = PropertyCleaner.parse_int(bed_val)
                if bath_val:
                    res["bathrooms"] = PropertyCleaner.parse_int(bath_val)
                continue

            text_val = item.get("text") if isinstance(item, dict) else str(item)
            if not text_val:
                continue
            text_str = str(text_val).strip()

            year_match = re.search(r"Built:\s*(\d{4})", text_str, re.IGNORECASE)
            if year_match:
                res["build_year"] = int(year_match.group(1))
                continue

            if any(w in text_str.lower() for w in ["leasehold", "freehold"]):
                res["tenure"] = text_str
                t_lower = text_str.lower()
                if "freehold" in t_lower:
                    res["tenure_category"] = "Freehold"
                elif "999-year" in t_lower:
                    res["tenure_category"] = "999-year Leasehold"
                elif "99-year" in t_lower:
                    res["tenure_category"] = "99-year Leasehold"
                else:
                    res["tenure_category"] = "Leasehold"
                continue

            if "sqft" in text_str.lower():
                land_match = re.search(r"([\d,]+(?:\.\d+)?)\s*sqft\s*\(land\)", text_str, re.IGNORECASE)
                if land_match:
                    res["land_area_sqft"] = PropertyCleaner.parse_numeric(land_match.group(1))
                floor_match = re.search(r"([\d,]+(?:\.\d+)?)\s*sqft(?:\s*\(floor\))?", text_str, re.IGNORECASE)
                if floor_match:
                    res["floor_area_sqft"] = PropertyCleaner.parse_numeric(floor_match.group(1))
                continue

            pt_types = ["condominium", "apartment", "hdb", "terraced house", "bungalow", "semi-detached", "landed", "shophouse"]
            if any(pt in text_str.lower() for pt in pt_types):
                res["property_type"] = text_str

        return res

    @staticmethod
    def parse_mrt(mrt_data: Any) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """Parse MRT transit info."""
        if not mrt_data or not isinstance(mrt_data, dict):
            return None, None, None

        text_str = mrt_data.get("nearbyText") or ""
        if not text_str:
            return None, None, None

        walking_mins = None
        min_match = re.search(r"(\d+)\s*min", text_str, re.IGNORECASE)
        if min_match:
            walking_mins = int(min_match.group(1))

        distance_m = None
        km_match = re.search(r"([\d.]+)\s*km", text_str, re.IGNORECASE)
        if km_match:
            distance_m = int(round(float(km_match.group(1)) * 1000))
        else:
            m_match = re.search(r"(\d+)\s*m\b", text_str, re.IGNORECASE)
            if m_match:
                distance_m = int(m_match.group(1))

        station_name = None
        from_match = re.search(r"from\s+(.+)$", text_str, re.IGNORECASE)
        if from_match:
            station_name = from_match.group(1).strip()

        return station_name, distance_m, walking_mins

    @classmethod
    def clean_homepage_images(cls, ld: Dict[str, Any]) -> Dict[str, Any]:
        """Extract separated images from homepage/search card."""
        thumbnail = ld.get("thumbnail")
        preview_items = []
        raw_items = ld.get("mediaCarousel", {}).get("previewMedia", {}).get("images", {}).get("items", [])
        
        for item in raw_items:
            if isinstance(item, dict) and item.get("src"):
                preview_items.append({
                    "src": item["src"],
                    "caption": item.get("caption") or ""
                })

        return {
            "thumbnail": thumbnail,
            "preview_images": preview_items,
            "preview_count": len(preview_items)
        }

    @classmethod
    def enrich_from_detail(cls, cleaned: Dict[str, Any], detail_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich a cleaned property record with exact address, coordinates,
        agent personal profile (photo, name, license, company, years with PropertyGuru),
        and full detail images.
        """
        if not detail_data or not isinstance(detail_data, dict):
            return cleaned

        # 1. Exact Location & Specific Address
        loc = detail_data.get("listingDetail", {}).get("location") or {}
        addr = loc.get("address") or {}
        pt = loc.get("point") or {}

        postal_code = addr.get("postalCode") or None
        street_number = str(addr.get("streetNumber")) if addr.get("streetNumber") is not None else None
        block = str(addr.get("block")) if addr.get("block") is not None else None
        unit = str(addr.get("unit")) if addr.get("unit") is not None else None
        floor_level = str(addr.get("floor")) if addr.get("floor") is not None else None
        street_name = loc.get("streetName") or loc.get("street") or None

        lat = cls.parse_numeric(pt.get("lat"))
        lon = cls.parse_numeric(pt.get("lon") or pt.get("lng"))

        if postal_code:
            cleaned["postal_code"] = str(postal_code).strip()
        if street_number:
            cleaned["street_number"] = street_number.strip()
        if block:
            cleaned["block"] = block.strip()
        if unit:
            cleaned["unit"] = unit.strip()
        if floor_level:
            cleaned["floor_level"] = floor_level.strip()
        if street_name:
            cleaned["street_name"] = street_name.strip()
        if lat is not None:
            cleaned["latitude"] = lat
        if lon is not None:
            cleaned["longitude"] = lon

        # 2. Agent Personal Profile
        cad = detail_data.get("contactAgentData", {}).get("contactAgentCard") or {}
        agent_info = cad.get("agentInfoProps", {}).get("agent") or {}
        tenure_info = cad.get("agentInfoProps", {}).get("tenure") or {}
        agency_info = cad.get("agency") or {}
        lister = detail_data.get("listingDetail", {}).get("lister", {}).get("metaByType", {}).get("agent") or {}

        agent_id = cls.parse_int(agent_info.get("id") or lister.get("id") or cleaned.get("agent_id"))
        agent_name = agent_info.get("name") or lister.get("name") or cleaned.get("agent_name")
        agent_avatar = agent_info.get("avatar") or (lister.get("avatar") or {}).get("src")
        agency_name = agency_info.get("name") or cleaned.get("agency_name")
        
        license_no = lister.get("license") or cleaned.get("agent_license")
        if not license_no and agent_info.get("description"):
            m = re.search(r"CEA:\s*([A-Z0-9]+)", agent_info["description"])
            if m:
                license_no = m.group(1)

        years_str = None
        years_num = None
        if tenure_info:
            years_num = tenure_info.get("years")
            text = tenure_info.get("text", "")
            suffix = tenure_info.get("suffix", "")
            years_str = f"{text} {suffix}".strip() if text else None

        agent_phone = agent_info.get("mobile")
        if not agent_phone and lister.get("contacts"):
            agent_phone = lister["contacts"][0].get("value")

        cleaned["agent_id"] = agent_id
        cleaned["agent_name"] = agent_name
        cleaned["agent_avatar"] = agent_avatar
        cleaned["agent_license"] = license_no
        cleaned["agency_name"] = agency_name
        cleaned["agent_years_with_pg"] = years_str
        cleaned["agent_years_count"] = years_num
        cleaned["agent_phone"] = agent_phone
        cleaned["agent_profile_url"] = agent_info.get("profileUrl")

        # 3. Project ID & Price Insights
        proj = detail_data.get("listingDetail", {}).get("project") or {}
        proj_id = cls.parse_int(
            proj.get("id") or 
            proj.get("projectId") or 
            (detail_data.get("listingData", {}).get("property") or {}).get("id") or
            cleaned.get("project_id")
        )
        if proj_id is not None:
            cleaned["project_id"] = proj_id

        # Price insights summary
        pi = detail_data.get("priceInsightData", {})
        if pi:
            cleaned["price_insights"] = {
                "srpSimilarListingUrl": pi.get("srpSimilarListingUrl"),
                "shouldHidePriceHistoryComparison": pi.get("shouldHidePriceHistoryComparison")
            }

        # 4. Detail Page Images
        mg = detail_data.get("mediaGalleryData", {}).get("media") or {}
        
        detail_photos = []
        for item in mg.get("images", {}).get("items", []):
            if isinstance(item, dict) and item.get("src"):
                detail_photos.append({
                    "src": item["src"],
                    "caption": item.get("caption") or ""
                })

        detail_floorplans = []
        for item in mg.get("floorPlans", {}).get("items", []):
            if isinstance(item, dict) and item.get("src"):
                detail_floorplans.append({
                    "src": item["src"],
                    "caption": item.get("caption") or ""
                })

        cleaned["detail_images"] = {
            "photos": detail_photos,
            "floor_plans": detail_floorplans,
            "photo_count": len(detail_photos),
            "floor_plan_count": len(detail_floorplans)
        }
        cleaned["detail_fetched"] = True

        return cleaned

    @classmethod
    def extract_agent_record(cls, cleaned: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract a row for the agents table from an enriched listing."""
        aid = cleaned.get("agent_id")
        if not aid:
            return None
        return {
            "agent_id": aid,
            "name": cleaned.get("agent_name") or "Agent",
            "avatar_url": cleaned.get("agent_avatar"),
            "agency_name": cleaned.get("agency_name"),
            "license": cleaned.get("agent_license"),
            "years_with_pg": cleaned.get("agent_years_with_pg"),
            "years_count": cleaned.get("agent_years_count"),
            "phone": cleaned.get("agent_phone"),
            "profile_url": cleaned.get("agent_profile_url"),
        }

    @classmethod
    def parse_project_transactions(
        cls,
        html_text: str,
        project_id: int,
        project_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extract past URA / HDB price history transactions from a project page HTML.
        """
        records = []
        if not html_text:
            return records

        m = re.search(r"(\[\s*\{\s*\"building\".*?\])", html_text, re.DOTALL)
        if not m:
            return records

        try:
            txs = json.loads(m.group(1))
            for t in txs:
                if not isinstance(t, dict):
                    continue
                c_date_str = t.get("contract_date")
                c_date = None
                if c_date_str:
                    try:
                        c_date = date.fromisoformat(c_date_str)
                    except Exception:
                        pass

                price = cls.parse_numeric(t.get("price"))
                if price is None:
                    continue

                records.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "contract_date": c_date,
                    "price": price,
                    "psf": cls.parse_numeric(t.get("psf")),
                    "building": t.get("building"),
                    "floor_level": str(t.get("level")) if t.get("level") is not None else None,
                    "size_sqft": cls.parse_numeric(t.get("size")),
                    "bedrooms": cls.parse_int(t.get("bedrooms")),
                    "transaction_type": (t.get("transaction_type") or "sales").lower(),
                    "property_type": t.get("property_type"),
                    "district_code": t.get("district_code"),
                    "postal_code": str(t.get("postcode")) if t.get("postcode") else None,
                    "raw_json": t
                })
        except Exception as e:
            logger.error(f"Failed to parse project transactions: {e}")

        return records

    @classmethod
    def extract_separated_image_records(cls, cleaned: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Converts separated images into rows for property_images table."""
        records = []
        lid = cleaned.get("listing_id")
        if not lid:
            return records

        # Homepage Images
        hp = cleaned.get("homepage_images") or {}
        if hp.get("thumbnail"):
            records.append({
                "listing_id": lid,
                "source_page": "HOMEPAGE",
                "image_type": "THUMBNAIL",
                "image_url": hp["thumbnail"],
                "caption": "Search card thumbnail",
                "display_order": 0
            })
        for i, item in enumerate(hp.get("preview_images", [])):
            records.append({
                "listing_id": lid,
                "source_page": "HOMEPAGE",
                "image_type": "PREVIEW",
                "image_url": item["src"],
                "caption": item.get("caption"),
                "display_order": i + 1
            })

        # Detail Page Images
        dp = cleaned.get("detail_images") or {}
        for i, item in enumerate(dp.get("photos", [])):
            records.append({
                "listing_id": lid,
                "source_page": "DETAIL_PAGE",
                "image_type": "PHOTO",
                "image_url": item["src"],
                "caption": item.get("caption"),
                "display_order": i
            })
        for i, item in enumerate(dp.get("floor_plans", [])):
            records.append({
                "listing_id": lid,
                "source_page": "DETAIL_PAGE",
                "image_type": "FLOOR_PLAN",
                "image_url": item["src"],
                "caption": item.get("caption"),
                "display_order": i
            })

        return records

    @classmethod
    def clean_listing(cls, raw: Dict[str, Any], default_type: str = "SALE") -> Optional[Dict[str, Any]]:
        """Transform a raw PropertyGuru listingData dictionary into a normalized database record."""
        ld = raw.get("listingData", raw)
        if not isinstance(ld, dict):
            return None

        listing_id = cls.parse_int(ld.get("id"))
        if not listing_id:
            return None

        listing_type = (ld.get("typeCode") or default_type or "SALE").upper()
        title = ld.get("localizedTitle") or ld.get("title") or ""
        title = title.strip()[:255] if title else None

        price, currency, price_type = cls.parse_price_info(ld.get("price"))
        direct_floor_area = cls.parse_numeric(ld.get("floorArea"))
        features_info = cls.parse_features(ld.get("listingFeatures", []))
        
        floor_area_sqft = direct_floor_area or features_info.get("floor_area_sqft")
        land_area_sqft = features_info.get("land_area_sqft")
        psf = cls.parse_psf(ld.get("psfText"), price, floor_area_sqft)

        bedrooms = cls.parse_int(ld.get("bedrooms"))
        if bedrooms is None:
            bedrooms = features_info.get("bedrooms")
            
        bathrooms = cls.parse_int(ld.get("bathrooms"))
        if bathrooms is None:
            bathrooms = features_info.get("bathrooms")

        prop_obj = ld.get("property") or {}
        property_type = (
            prop_obj.get("subTypeText") or 
            prop_obj.get("typeText") or 
            features_info.get("property_type")
        )
        if property_type:
            property_type = property_type.strip()[:100]

        add_data = ld.get("additionalData") or {}
        tenure = features_info.get("tenure")
        tenure_category = features_info.get("tenure_category", "Other")
        if not tenure and add_data.get("tenure"):
            t_code = add_data.get("tenure")
            if t_code == "F":
                tenure = "Freehold"
                tenure_category = "Freehold"
            elif t_code == "L99":
                tenure = "99-year Leasehold"
                tenure_category = "99-year Leasehold"
            elif t_code == "L999":
                tenure = "999-year Leasehold"
                tenure_category = "999-year Leasehold"

        build_year = features_info.get("build_year")

        full_address = ld.get("fullAddress")
        short_address = ld.get("shortAddress")
        district_code = add_data.get("districtCode") or None
        district_text = add_data.get("districtText") or None
        region_code = add_data.get("regionCode") or None
        region_text = add_data.get("regionText") or None

        nearest_mrt, mrt_distance_m, mrt_walking_mins = cls.parse_mrt(ld.get("mrt"))

        # Agent & Agency basic from SRP
        agent_obj = ld.get("agent") or {}
        agency_obj = ld.get("agency") or {}
        agent_id = cls.parse_int(agent_obj.get("id"))
        agent_name = agent_obj.get("name")
        agent_license = agent_obj.get("license")
        agency_id = cls.parse_int(agency_obj.get("id"))
        agency_name = agency_obj.get("name")
        agent_avatar = (agent_obj.get("avatar") or {}).get("src") if isinstance(agent_obj.get("avatar"), dict) else None

        posted_at = None
        posted_obj = ld.get("postedOn") or {}
        if isinstance(posted_obj, dict) and posted_obj.get("unix"):
            try:
                posted_at = datetime.fromtimestamp(int(posted_obj["unix"]), tz=timezone.utc)
            except Exception:
                pass

        hp_images = cls.clean_homepage_images(ld)
        proj_id = cls.parse_int(prop_obj.get("id"))

        return {
            "listing_id": listing_id,
            "listing_type": listing_type,
            "transaction_category": "买房/出售" if (listing_type or "").upper() == "SALE" else "租房/出租",
            "title": title,
            "property_type": property_type,
            "project_id": proj_id,
            "price": price,
            "currency": currency,
            "price_type": price_type,
            "psf": psf,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "floor_area_sqft": floor_area_sqft,
            "land_area_sqft": land_area_sqft,
            "tenure": tenure,
            "tenure_category": tenure_category,
            "build_year": build_year,
            "full_address": full_address,
            "short_address": short_address,
            "district_code": district_code,
            "district_text": district_text,
            "region_code": region_code,
            "region_text": region_text,
            "postal_code": None,
            "street_name": None,
            "street_number": None,
            "block": None,
            "unit": None,
            "floor_level": None,
            "latitude": None,
            "longitude": None,
            "nearest_mrt": nearest_mrt,
            "mrt_distance_m": mrt_distance_m,
            "mrt_walking_mins": mrt_walking_mins,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "agent_avatar": agent_avatar,
            "agent_license": agent_license,
            "agent_years_with_pg": None,
            "agent_years_count": None,
            "agent_phone": None,
            "agency_id": agency_id,
            "agency_name": agency_name,
            "url": ld.get("url"),
            "thumbnail_url": ld.get("thumbnail"),
            "is_verified": bool(ld.get("isVerified", False)),
            "is_official": bool(ld.get("isOfficialListing", False)),
            "posted_at": posted_at,
            "detail_fetched": False,
            "homepage_images": hp_images,
            "detail_images": None,
            "price_insights": None,
            "raw_json": ld
        }
