"""TMS 602 / ACI 530.1 hot- and cold-weather masonry (brick, CMU, mortar)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

from .formulas import (
    EVAP_NOGO_LB_FT2_HR,
    EVAP_WARNING_LB_FT2_HR,
    HEAVY_RAIN_IN,
    HIGH_WIND_MPH,
    RAIN_WARNING_IN,
    WeatherHour,
    _floor_hour,
    _hour_index,
    _min_temp,
    assumed_concrete_temp_f,
    evaporation_rate_lb_ft2_hr,
)
from .schemas import (
    GoNoGoStatus,
    HourlyPoint,
    MasonryDesign,
    Metrics,
    Predictions,
    RiskFactorDetail,
)

# TMS 602: below 40°F is cold-weather masonry; below 20°F needs a heated enclosure.
COLD_MASONRY_F = 40.0
ENCLOSURE_F = 20.0
FROZEN_UNIT_F = 32.0
HOT_MASONRY_F = 100.0

RISK_COPY = {
    "COLD_WEATHER_MASONRY": (
        "Cold-weather masonry",
        "Air is forecast below 40°F (TMS 602 / ACI 530.1). Heat mixing water, do not lay frozen units, and protect the wall after laying.",
    ),
    "MASONRY_BELOW_20F": (
        "Below 20°F — heated enclosure",
        "TMS 602 requires a heated enclosure and 40°F maintained for 24 hours. Without that plan this is not a routine lay-up.",
    ),
    "FROZEN_UNITS": (
        "Units likely at or below freezing",
        "Do not lay masonry units that are frozen or have ice or snow on the bed. Warm units or delay.",
    ),
    "HOT_WEATHER_MASONRY": (
        "Hot-weather masonry",
        "Air at or above 100°F. Mortar can flash-set and lose bond. Shade, wet CMU (not brick unless specified), and cover the wall.",
    ),
    "HIGH_EVAPORATION_RATE": (
        "Rapid mortar drying",
        "Evaporation exceeds 0.2 lb/ft²/hr. Joints can dry before bond develops. Use windbreaks, fogging, or wet the units.",
    ),
    "EXTREME_EVAPORATION_RATE": (
        "Extreme drying",
        "Evaporation exceeds 0.5 lb/ft²/hr. Delay or provide a full hot-weather masonry plan.",
    ),
    "RAIN_DURING_POUR": (
        "Rain during laying",
        "Precipitation is forecast during the lay-up. Rain on fresh mortar weakens bond and stains the face.",
    ),
    "HEAVY_RAIN_DURING_POUR": (
        "Heavy rain during laying",
        "Meaningful rain during placement. Do not lay unless the work is fully covered.",
    ),
    "HIGH_WIND": (
        "High wind",
        "Wind at 15 mph or more dries mortar joints and cools the wall. Use windbreaks.",
    ),
}

MITIGATION = {
    "MASONRY_BELOW_20F": "Do not lay without a heated enclosure that holds 40°F for 24 hours after construction.",
    "FROZEN_UNITS": "Remove ice and snow; warm the units. Do not lay frozen masonry.",
    "COLD_WEATHER_MASONRY": "Heat mixing water, keep mortar 40–120°F, and cover the wall per TMS 602 for the temperature band.",
    "HOT_WEATHER_MASONRY": "Shade the wall, use cooler mixing water, and cover completed work. Pre-wet CMU if the spec allows.",
    "HIGH_EVAPORATION_RATE": "Windbreaks and fogging, or wet high-suction units so mortar does not dry in the joint.",
    "EXTREME_EVAPORATION_RATE": "Delay the lay-up or set up shade, windbreaks, and a wetting plan before placing mortar.",
    "HEAVY_RAIN_DURING_POUR": "Delay until the rain passes, or fully tent the work.",
    "RAIN_DURING_POUR": "Have covers ready, or shift the start time.",
    "HIGH_WIND": "Erect windbreaks on the windward side.",
}

GO_MITIGATION = "No special hot- or cold-weather masonry protection is indicated from this forecast. Follow normal TMS 602 practice."


def evaluate_masonry(
    pour_time: datetime,
    hourly: Sequence[WeatherHour],
    design: MasonryDesign,
    mortar_temp_f: Optional[float] = None,
) -> dict:
    if not hourly:
        raise ValueError("No hourly weather data available for this pour window.")

    pour_time = _floor_hour(pour_time)
    idx = _hour_index(hourly, pour_time)
    pour_hour = hourly[idx]
    window_24 = [h for h in hourly[idx:] if h.time < pour_time + timedelta(hours=24)]
    window_48 = [h for h in hourly[idx:] if h.time < pour_time + timedelta(hours=48)]
    rain_window = hourly[idx : idx + 3]

    mortar_temp = assumed_concrete_temp_f(pour_hour.temp_f, mortar_temp_f)
    evap = evaporation_rate_lb_ft2_hr(
        concrete_temp_f=mortar_temp,
        air_temp_f=pour_hour.temp_f,
        rh_pct=pour_hour.rh_pct,
        wind_mph=pour_hour.wind_mph,
    )
    min_24 = _min_temp(window_24)
    min_48 = _min_temp(window_48)
    rain_at_pour = max((h.precip_in for h in rain_window), default=0.0)

    codes: list[str] = []
    if min_24 is not None and min_24 < ENCLOSURE_F:
        codes.append("MASONRY_BELOW_20F")
    elif pour_hour.temp_f < FROZEN_UNIT_F or (min_24 is not None and min_24 < FROZEN_UNIT_F):
        codes.append("FROZEN_UNITS")
    elif min_48 is not None and min_48 < COLD_MASONRY_F:
        codes.append("COLD_WEATHER_MASONRY")

    if evap >= EVAP_NOGO_LB_FT2_HR:
        codes.append("EXTREME_EVAPORATION_RATE")
    elif evap >= EVAP_WARNING_LB_FT2_HR:
        codes.append("HIGH_EVAPORATION_RATE")

    if pour_hour.temp_f >= HOT_MASONRY_F:
        codes.append("HOT_WEATHER_MASONRY")

    if rain_at_pour >= HEAVY_RAIN_IN:
        codes.append("HEAVY_RAIN_DURING_POUR")
    elif rain_at_pour >= RAIN_WARNING_IN:
        codes.append("RAIN_DURING_POUR")

    if pour_hour.wind_mph >= HIGH_WIND_MPH and "HIGH_EVAPORATION_RATE" not in codes and "EXTREME_EVAPORATION_RATE" not in codes:
        codes.append("HIGH_WIND")

    nogo = {"MASONRY_BELOW_20F", "EXTREME_EVAPORATION_RATE", "HEAVY_RAIN_DURING_POUR"}
    if any(code in nogo for code in codes):
        status = GoNoGoStatus.NO_GO
    elif codes:
        status = GoNoGoStatus.WARNING
    else:
        status = GoNoGoStatus.GO

    details = [
        RiskFactorDetail(code=code, label=RISK_COPY[code][0], detail=RISK_COPY[code][1])
        for code in codes
        if code in RISK_COPY
    ]
    mitigation = GO_MITIGATION
    if codes:
        parts = [MITIGATION[code] for code in codes if code in MITIGATION]
        mitigation = " ".join(parts) if parts else GO_MITIGATION

    chart_hours: list[HourlyPoint] = []
    for hour in window_48:
        hour_mortar = assumed_concrete_temp_f(hour.temp_f, mortar_temp_f)
        chart_hours.append(
            HourlyPoint(
                time=hour.time.isoformat(),
                temp_f=round(hour.temp_f, 1),
                relative_humidity_pct=round(hour.rh_pct, 1),
                wind_speed_mph=round(hour.wind_mph, 1),
                precipitation_in=round(hour.precip_in, 3),
                evaporation_rate_lbs_sqft_hr=evaporation_rate_lb_ft2_hr(
                    concrete_temp_f=hour_mortar,
                    air_temp_f=hour.temp_f,
                    rh_pct=hour.rh_pct,
                    wind_mph=hour.wind_mph,
                ),
            )
        )

    _ = design  # unit/mortar type stored with the check; thresholds are TMS ambient bands
    return {
        "go_no_go_status": status,
        "risk_factors": codes,
        "risk_factor_details": details,
        "metrics": Metrics(
            ambient_temp_f=round(pour_hour.temp_f, 1),
            relative_humidity_pct=round(pour_hour.rh_pct, 1),
            wind_speed_mph=round(pour_hour.wind_mph, 1),
            precipitation_in=round(pour_hour.precip_in, 3),
            concrete_temp_f=round(mortar_temp, 1),
            calculated_evaporation_rate_lbs_sqft_hr=evap,
            min_temp_next_24h_f=round(min_24, 1) if min_24 is not None else None,
            min_temp_next_48h_f=round(min_48, 1) if min_48 is not None else None,
        ),
        "predictions": Predictions(protection_period_hours=24),
        "recommended_mitigation": mitigation,
        "hourly": chart_hours,
    }
