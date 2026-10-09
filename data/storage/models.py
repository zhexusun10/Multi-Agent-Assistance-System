from datetime import datetime
from sqlalchemy import (
    Column, BigInteger, String, Numeric, Integer, Boolean,
    Text, DateTime, Date, Index, ForeignKey, UniqueConstraint, func, JSON
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
Base = declarative_base()

class Property(Base):
    __tablename__ = "properties"

    listing_id = Column(BigInteger, primary_key=True, comment="PropertyGuru listing ID")
    listing_type = Column(String(20), nullable=False, index=True, comment="SALE or RENT")
    transaction_category = Column(String(50), nullable=True, index=True, comment="买房/出售 或 租房/出租")
    title = Column(String(255), nullable=True, comment="Project or listing title")
    property_type = Column(String(100), nullable=True, index=True, comment="Condo, HDB, Landed, etc.")
    project_id = Column(BigInteger, nullable=True, index=True, comment="Project / Condo ID")
    
    # Financial fields
    price = Column(Numeric(15, 2), nullable=True, index=True, comment="Total price in SGD")
    currency = Column(String(10), default="SGD", nullable=True)
    price_type = Column(String(50), nullable=True, comment="Negotiable, Guide Price, etc.")
    psf = Column(Numeric(10, 2), nullable=True, index=True, comment="Price per square foot")
    
    # Unit specifications
    bedrooms = Column(Integer, nullable=True, comment="Number of bedrooms")
    bathrooms = Column(Integer, nullable=True, comment="Number of bathrooms")
    floor_area_sqft = Column(Numeric(12, 2), nullable=True, comment="Floor area in sqft")
    land_area_sqft = Column(Numeric(12, 2), nullable=True, comment="Land area in sqft (if landed)")
    
    # Building details
    tenure = Column(String(100), nullable=True, comment="Tenure text, e.g. 99-year Leasehold")
    tenure_category = Column(String(50), nullable=True, index=True, comment="Normalized tenure category")
    build_year = Column(Integer, nullable=True, comment="Construction / completion year")
    
    # General Address & Region
    full_address = Column(Text, nullable=True)
    short_address = Column(Text, nullable=True)
    district_code = Column(String(20), nullable=True, index=True, comment="e.g. D05, D21, D10, D03, D04")
    district_text = Column(String(255), nullable=True, comment="District name")
    region_code = Column(String(20), nullable=True)
    region_text = Column(String(255), nullable=True)
    
    # Exact Specific Address (From Detail Page)
    postal_code = Column(String(20), nullable=True, index=True, comment="Exact Singapore 6-digit postal code")
    street_name = Column(String(255), nullable=True, comment="Exact street name")
    street_number = Column(String(50), nullable=True, comment="House/Street number")
    block = Column(String(50), nullable=True, comment="Block number")
    unit = Column(String(50), nullable=True, comment="Unit number if available")
    floor_level = Column(String(50), nullable=True, comment="Floor level if available")
    latitude = Column(Numeric(11, 8), nullable=True, comment="Exact geo latitude")
    longitude = Column(Numeric(11, 8), nullable=True, comment="Exact geo longitude")

    # Transit / MRT
    nearest_mrt = Column(String(255), nullable=True, comment="Nearest MRT station name")
    mrt_distance_m = Column(Integer, nullable=True, comment="Distance to MRT in meters")
    mrt_walking_mins = Column(Integer, nullable=True, comment="Walking duration to MRT in minutes")
    
    # Agent & Agency Details
    agent_id = Column(BigInteger, nullable=True, index=True)
    agent_name = Column(String(255), nullable=True)
    agent_avatar = Column(Text, nullable=True, comment="Agent photo URL")
    agent_license = Column(String(50), nullable=True, comment="CEA License Number")
    agent_years_with_pg = Column(String(50), nullable=True, comment="e.g. 10 years with PropertyGuru")
    agent_years_count = Column(Integer, nullable=True, comment="Numeric years with PropertyGuru")
    agent_phone = Column(String(50), nullable=True)
    agent_profile_url = Column(Text, nullable=True, comment="Agent profile page URL")
    agency_id = Column(BigInteger, nullable=True)
    agency_name = Column(String(255), nullable=True)
    
    # URLs & Separated Media Collections
    url = Column(Text, nullable=True)
    thumbnail_url = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=False)
    is_official = Column(Boolean, default=False)
    detail_fetched = Column(Boolean, default=False, index=True, comment="Whether detail page has been enriched")

    # Separated Images (JSONB / JSON)
    homepage_images = Column(JSON_TYPE, nullable=True, comment="Images from homepage/list: thumbnail & card previews")
    detail_images = Column(JSON_TYPE, nullable=True, comment="Images from detail page: full photos & floor plans")
    price_insights = Column(JSON_TYPE, nullable=True, comment="Price comparison & insights data")

    # Metadata
    posted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    raw_json = Column(JSON_TYPE, nullable=True, comment="Raw listing data from source")

    # Relationship to separated images table
    images = relationship("PropertyImage", back_populates="property", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_properties_type_price", "listing_type", "price"),
        Index("idx_properties_type_district", "listing_type", "district_code"),
        Index("idx_properties_postal_code", "postal_code"),
        Index("idx_properties_agent_id", "agent_id"),
        Index("idx_properties_raw_json", "raw_json", postgresql_using="gin"),
        Index("idx_properties_homepage_images", "homepage_images", postgresql_using="gin"),
        Index("idx_properties_detail_images", "detail_images", postgresql_using="gin"),
    )

    def to_dict(self):
        return {
            "listing_id": self.listing_id,
            "listing_type": self.listing_type,
            "title": self.title,
            "property_type": self.property_type,
            "price": float(self.price) if self.price is not None else None,
            "psf": float(self.psf) if self.psf is not None else None,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "floor_area_sqft": float(self.floor_area_sqft) if self.floor_area_sqft is not None else None,
            "postal_code": self.postal_code,
            "street_name": self.street_name,
            "street_number": self.street_number,
            "block": self.block,
            "latitude": float(self.latitude) if self.latitude is not None else None,
            "longitude": float(self.longitude) if self.longitude is not None else None,
            "district_code": self.district_code,
            "agent_name": self.agent_name,
            "agent_license": self.agent_license,
            "agency_name": self.agency_name,
            "agent_years_with_pg": self.agent_years_with_pg,
            "homepage_images_count": len(self.homepage_images.get("preview_images", [])) if self.homepage_images else 0,
            "detail_photos_count": self.detail_images.get("photo_count", 0) if self.detail_images else 0,
            "detail_floorplans_count": self.detail_images.get("floor_plan_count", 0) if self.detail_images else 0,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
        }


class PropertyImage(Base):
    __tablename__ = "property_images"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    listing_id = Column(BigInteger, ForeignKey("properties.listing_id", ondelete="CASCADE"), nullable=False, index=True)
    source_page = Column(String(20), nullable=False, index=True, comment="HOMEPAGE or DETAIL_PAGE")
    image_type = Column(String(20), nullable=False, index=True, comment="THUMBNAIL, PREVIEW, PHOTO, FLOOR_PLAN")
    image_url = Column(Text, nullable=False)
    caption = Column(Text, nullable=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    property = relationship("Property", back_populates="images")

    __table_args__ = (
        Index("idx_property_images_listing_source", "listing_id", "source_page"),
        UniqueConstraint("listing_id", "source_page", "image_url", name="uq_listing_source_image"),
    )


class Agent(Base):
    __tablename__ = "agents"

    agent_id = Column(BigInteger, primary_key=True, comment="PropertyGuru Agent ID")
    name = Column(String(255), nullable=False, index=True)
    avatar_url = Column(Text, nullable=True, comment="Profile photo URL")
    agency_name = Column(String(255), nullable=True, index=True, comment="Agency / Real Estate Firm")
    license = Column(String(50), nullable=True, index=True, comment="CEA License Number")
    years_with_pg = Column(String(50), nullable=True, comment="e.g. 10 years with PropertyGuru")
    years_count = Column(Integer, nullable=True, comment="Numeric years")
    phone = Column(String(50), nullable=True)
    profile_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "agency_name": self.agency_name,
            "license": self.license,
            "years_with_pg": self.years_with_pg,
            "phone": self.phone,
        }


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, nullable=True, index=True, comment="PropertyGuru Project ID")
    project_name = Column(String(255), nullable=True, index=True)
    contract_date = Column(Date, nullable=True, index=True, comment="Transaction contract date")
    price = Column(Numeric(15, 2), nullable=False, comment="Transacted price in SGD")
    psf = Column(Numeric(10, 2), nullable=True, comment="Price per square foot")
    building = Column(String(100), nullable=True, comment="Block or Building name, e.g. Blk 5A")
    floor_level = Column(String(50), nullable=True, comment="e.g. 04 or 01 to 05")
    size_sqft = Column(Numeric(10, 2), nullable=True, comment="Unit size in sqft")
    bedrooms = Column(Integer, nullable=True, comment="Number of bedrooms")
    transaction_type = Column(String(50), nullable=True, index=True, comment="sales or rent")
    property_type = Column(String(100), nullable=True, comment="Condo, Apartment, HDB, etc.")
    district_code = Column(String(20), nullable=True, index=True)
    postal_code = Column(String(20), nullable=True)
    raw_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_price_history_proj_date", "project_id", "contract_date"),
        UniqueConstraint("project_id", "contract_date", "building", "floor_level", "size_sqft", "price", name="uq_price_history_record"),
    )
