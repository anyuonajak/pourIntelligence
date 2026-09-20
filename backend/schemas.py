import re
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CementType(str, Enum):
    TYPE_I = "Type_I"
    TYPE_II = "Type_II"
    TYPE_III = "Type_III"
    TYPE_IL = "Type_IL"


class GoNoGoStatus(str, Enum):
    GO = "GO"
    WARNING = "WARNING"
    NO_GO = "NO_GO"


class MixDesign(BaseModel):
    cement_type: CementType = CementType.TYPE_I
    target_psi: int = Field(default=4000, ge=2500, le=10000)
    thickness_inches: float = Field(default=4, ge=1, le=36)


class UnitType(str, Enum):
    CMU = "CMU"
    BRICK = "brick"
    STONE = "stone"


class MortarType(str, Enum):
    TYPE_N = "Type_N"
    TYPE_S = "Type_S"
    TYPE_M = "Type_M"


class MasonryDesign(BaseModel):
    unit_type: UnitType = UnitType.CMU
    mortar_type: MortarType = MortarType.TYPE_N


class ProductType(str, Enum):
    CONCRETE = "concrete"
    MASONRY = "masonry"


class PourReadinessRequest(BaseModel):
    product: ProductType = ProductType.CONCRETE
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    zip_code: Optional[str] = Field(default=None, description="US 5-digit ZIP")
    address: Optional[str] = Field(default=None, description="Free-text place name or address")
    pour_date: datetime
    mix_design: MixDesign = Field(default_factory=MixDesign)
    masonry_design: MasonryDesign = Field(default_factory=MasonryDesign)
    concrete_temp_f: Optional[float] = Field(
        default=None,
        description="Fresh concrete or mortar temperature. If omitted, approximated as air temperature + 5°F.",
    )
    email: Optional[str] = Field(
        default=None,
        max_length=254,
        description="Optional address for critical alerts and the daily digest.",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if len(text) > 254 or not _EMAIL_RE.fullmatch(text):
            raise ValueError("Provide a valid email or leave it blank.")
        return text

    @model_validator(mode="after")
    def require_a_location(self) -> "PourReadinessRequest":
        has_coords = self.latitude is not None and self.longitude is not None
        has_zip = bool(self.zip_code and self.zip_code.strip())
        has_address = bool(self.address and self.address.strip())
        if not (has_coords or has_zip or has_address):
            raise ValueError("Provide latitude/longitude, a US zip_code, or an address.")
        return self


class LocationInfo(BaseModel):
    name: str
    latitude: float
    longitude: float
    timezone: Optional[str] = None


class RiskFactorDetail(BaseModel):
    code: str
    label: str
    detail: str


class Metrics(BaseModel):
    ambient_temp_f: float
    relative_humidity_pct: float
    wind_speed_mph: float
    precipitation_in: float
    concrete_temp_f: float
    calculated_evaporation_rate_lbs_sqft_hr: float
    min_temp_next_24h_f: Optional[float] = None
    min_temp_next_48h_f: Optional[float] = None


class Predictions(BaseModel):
    estimated_time_to_500_psi_hours: Optional[int] = None
    estimated_days_to_70_percent_strength: Optional[float] = None
    protection_period_hours: Optional[int] = None


class HourlyPoint(BaseModel):
    time: str
    temp_f: float
    relative_humidity_pct: float
    wind_speed_mph: float
    precipitation_in: float
    evaporation_rate_lbs_sqft_hr: float


DISCLAIMER = (
    "Advisory only. This is not a substitute for ACI 305R/306R, TMS 602 / ACI 530.1, "
    "project specifications, or the engineer of record. After you submit a ticket we keep "
    "watching the forecast and will update the stamp if weather moves enough to change the call."
)


class WatchChange(BaseModel):
    code: str
    label: str
    detail: str
    previous: str = ""
    current: str = ""


class PourReadinessResponse(BaseModel):
    api_version: str = "v1"
    product: ProductType = ProductType.CONCRETE
    check_id: Optional[UUID] = None
    go_no_go_status: GoNoGoStatus
    risk_factors: list[str]
    risk_factor_details: list[RiskFactorDetail]
    metrics: Metrics
    predictions: Predictions
    recommended_mitigation: str
    location: LocationInfo
    hourly: list[HourlyPoint]
    watching: bool = False
    watch_until: Optional[str] = None
    next_check_seconds: Optional[int] = None
    watch_limit_reached: bool = False
    disclaimer: str = DISCLAIMER


class WatchBaseline(BaseModel):
    """What the caller was last told, so we only report real movement."""

    go_no_go_status: Optional[GoNoGoStatus] = None
    risk_factors: list[str] = Field(default_factory=list)
    metrics: Optional[Metrics] = None


class PourWatchRequest(PourReadinessRequest):
    check_id: Optional[UUID] = None
    baseline: Optional[WatchBaseline] = None


class PourWatchResponse(BaseModel):
    api_version: str = "v1"
    product: ProductType = ProductType.CONCRETE
    check_id: Optional[UUID] = None
    watching: bool
    watch_until: Optional[str] = None
    checked_at: str
    next_check_seconds: int
    changed: bool
    changes: list[WatchChange]
    go_no_go_status: GoNoGoStatus
    risk_factors: list[str]
    risk_factor_details: list[RiskFactorDetail]
    metrics: Metrics
    predictions: Predictions
    recommended_mitigation: str
    location: LocationInfo
    hourly: list[HourlyPoint]
    disclaimer: str = DISCLAIMER


class PourOutcome(str, Enum):
    SUCCESS = "success"
    CRACKED = "cracked"
    DELAYED = "delayed"
    OTHER = "other"


class PourOutcomeRequest(BaseModel):
    check_id: UUID
    outcome: PourOutcome
    notes: Optional[str] = Field(default=None, max_length=500)


class PourOutcomeResponse(BaseModel):
    api_version: str = "v1"
    recorded: bool
    check_id: UUID
    outcome: PourOutcome
    disclaimer: str = DISCLAIMER
