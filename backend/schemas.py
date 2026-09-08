from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


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


class PourReadinessRequest(BaseModel):
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    zip_code: Optional[str] = Field(default=None, description="US 5-digit ZIP")
    address: Optional[str] = Field(default=None, description="Free-text place name or address")
    pour_date: datetime
    mix_design: MixDesign = Field(default_factory=MixDesign)
    concrete_temp_f: Optional[float] = Field(
        default=None,
        description="Fresh concrete temperature. If omitted, approximated as air temperature + 5°F.",
    )

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


class HourlyPoint(BaseModel):
    time: str
    temp_f: float
    relative_humidity_pct: float
    wind_speed_mph: float
    precipitation_in: float
    evaporation_rate_lbs_sqft_hr: float


class PourReadinessResponse(BaseModel):
    go_no_go_status: GoNoGoStatus
    risk_factors: list[str]
    risk_factor_details: list[RiskFactorDetail]
    metrics: Metrics
    predictions: Predictions
    recommended_mitigation: str
    location: LocationInfo
    hourly: list[HourlyPoint]
    disclaimer: str = (
        "Advisory only. This is not a substitute for ACI 305R/306R, project specifications, "
        "or the engineer of record. Weather forecasts change; re-check on pour day."
    )
