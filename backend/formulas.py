"""ACI 305R / 306R pour-readiness calculations.

Hot-weather evaporation uses Uno's closed-form of Menzel's formula
(ACI Materials Journal, 1998), the equation behind the ACI 305R nomograph.

Cold-weather checks follow ACI 306R thresholds (40°F cold-weather definition,
32°F freezing, protect until ~500 psi). Strength-gain timing uses a simplified
Nurse-Saul maturity method.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from .schemas import (
    CementType,
    GoNoGoStatus,
    HourlyPoint,
    MixDesign,
    Metrics,
    Predictions,
    RiskFactorDetail,
)

# ACI 305R: plastic shrinkage cracking becomes a concern above this rate.
EVAP_WARNING_LB_FT2_HR = 0.20
# Extreme rate — typically not manageable with routine mitigation.
EVAP_NOGO_LB_FT2_HR = 0.50

COLD_WEATHER_F = 40.0
FREEZING_F = 32.0
NURSE_SAUL_DATUM_F = 32.0
HIGH_AMBIENT_F = 90.0
RAIN_WARNING_IN = 0.02
HEAVY_RAIN_IN = 0.15
HIGH_WIND_MPH = 15.0

# Nurse-Saul maturity (°F-hours above 32°F) to reach 500 psi at typical Type I
# behavior: ~12 h at 73°F → (73-32)*12 ≈ 492.
MATURITY_500_PSI = {
    CementType.TYPE_I: 500.0,
    CementType.TYPE_II: 600.0,
    CementType.TYPE_III: 300.0,
    CementType.TYPE_IL: 550.0,
}

# 70% of specified strength, calibrated to ~7 days at 73°F for Type I.
MATURITY_70_PCT = {
    CementType.TYPE_I: 7 * 24 * (73.0 - NURSE_SAUL_DATUM_F),
    CementType.TYPE_II: 8 * 24 * (73.0 - NURSE_SAUL_DATUM_F),
    CementType.TYPE_III: 3 * 24 * (73.0 - NURSE_SAUL_DATUM_F),
    CementType.TYPE_IL: 7.5 * 24 * (73.0 - NURSE_SAUL_DATUM_F),
}

RISK_COPY = {
    "HIGH_EVAPORATION_RATE": (
        "High evaporation rate",
        "Surface moisture is leaving faster than 0.2 lb/ft²/hr. Plastic shrinkage cracking is likely without protection (ACI 305R).",
    ),
    "EXTREME_EVAPORATION_RATE": (
        "Extreme evaporation rate",
        "Evaporation exceeds 0.5 lb/ft²/hr. Routine curing compound or windbreaks are unlikely to be enough.",
    ),
    "COLD_WEATHER_CONDITIONS": (
        "Cold-weather concreting conditions",
        "Air temperature is forecast below 40°F during the protection period (ACI 306R). Plan blankets, heated mix, or an enclosure.",
    ),
    "FREEZING_BEFORE_500_PSI": (
        "Freezing before 500 psi",
        "The forecast drops below 32°F before the slab is expected to reach 500 psi. Frozen fresh concrete can be a failed pour.",
    ),
    "RAIN_DURING_POUR": (
        "Rain during the pour window",
        "Precipitation is forecast at or just after placement. Rain on plastic concrete can weaken the surface and wash cement.",
    ),
    "HEAVY_RAIN_DURING_POUR": (
        "Heavy rain during the pour",
        "A meaningful rain amount is forecast during placement. Delay unless the placement can be fully covered.",
    ),
    "HIGH_AMBIENT_TEMPERATURE": (
        "High ambient temperature",
        "Air temperature is at or above 90°F. Expect faster set, higher water demand, and more evaporation.",
    ),
    "HIGH_WIND": (
        "High wind at placement",
        "Wind at pour time is 15 mph or higher, which drives evaporation even when air temperature is moderate.",
    ),
}

MITIGATION = {
    "EXTREME_EVAPORATION_RATE": (
        "Do not pour without a full hot-weather plan: windbreaks, fogging, sunshades, a cooled mix, or a night pour."
    ),
    "HIGH_EVAPORATION_RATE": (
        "Apply curing compound immediately after finishing, or use windbreaks and fogging so the surface does not dry out."
    ),
    "FREEZING_BEFORE_500_PSI": (
        "Delay the pour or provide heated protection. Concrete is forecast to freeze before reaching 500 psi."
    ),
    "COLD_WEATHER_CONDITIONS": (
        "Use cold-weather protection (blankets, insulated forms, heated enclosure) and do not place on a frozen subgrade."
    ),
    "HEAVY_RAIN_DURING_POUR": "Delay the pour until the rain passes, or be prepared to fully cover the placement.",
    "RAIN_DURING_POUR": "Have covers ready, or shift the start time to miss the rain.",
    "HIGH_AMBIENT_TEMPERATURE": (
        "Consider a cooler mix, night placement, and continuous wet curing or evaporation retarders."
    ),
    "HIGH_WIND": "Erect windbreaks on the windward side of the slab to cut evaporation.",
}

GO_MITIGATION = "No special hot- or cold-weather protection is indicated from this forecast. Follow normal curing practice."


@dataclass(frozen=True)
class WeatherHour:
    time: datetime
    temp_f: float
    rh_pct: float
    wind_mph: float
    precip_in: float


def evaporation_rate_lb_ft2_hr(
    concrete_temp_f: float,
    air_temp_f: float,
    rh_pct: float,
    wind_mph: float,
) -> float:
    """Uno (1998) inch-pound form of Menzel's evaporation formula.

    E = (Tc^2.5 - r * Ta^2.5) * (1 + 0.4 V) * 10^-6
    where Tc/Ta are °F, r is RH as a decimal, V is wind in mph,
    and E is lb/ft²/hr.
    """
    tc = max(concrete_temp_f, 0.0)
    ta = max(air_temp_f, 0.0)
    r = min(max(rh_pct / 100.0, 0.0), 1.0)
    v = max(wind_mph, 0.0)
    rate = (tc**2.5 - r * ta**2.5) * (1.0 + 0.4 * v) * 1e-6
    return round(max(rate, 0.0), 4)


def assumed_concrete_temp_f(air_temp_f: float, provided: Optional[float]) -> float:
    if provided is not None:
        return provided
    return air_temp_f + 5.0


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _hour_index(hourly: Sequence[WeatherHour], pour_time: datetime) -> int:
    target = _floor_hour(pour_time)
    for i, hour in enumerate(hourly):
        if _floor_hour(hour.time) == target:
            return i
    for i, hour in enumerate(hourly):
        if hour.time >= target:
            return i
    raise ValueError("Pour time is outside the available forecast window.")


def _maturity_increment_f_hours(temp_f: float, hours: float = 1.0) -> float:
    return max(temp_f - NURSE_SAUL_DATUM_F, 0.0) * hours


def hours_to_maturity(
    start_index: int,
    hourly: Sequence[WeatherHour],
    required: float,
) -> Optional[float]:
    """Walk the forecast accumulating Nurse-Saul maturity. Extrapolate if needed."""
    if required <= 0:
        return 0.0
    accumulated = 0.0
    hours = 0.0
    last_temp = hourly[start_index].temp_f if start_index < len(hourly) else 70.0

    for hour in hourly[start_index:]:
        last_temp = hour.temp_f
        increment = _maturity_increment_f_hours(hour.temp_f)
        if accumulated + increment >= required:
            if increment <= 0:
                return None
            fraction = (required - accumulated) / increment
            return round(hours + fraction, 1)
        accumulated += increment
        hours += 1.0

    # Forecast ended; assume the last temperature continues.
    remaining = required - accumulated
    increment = _maturity_increment_f_hours(last_temp)
    if increment <= 0:
        return None
    return round(hours + remaining / increment, 1)


def _min_temp(hours: Sequence[WeatherHour]) -> Optional[float]:
    if not hours:
        return None
    return min(h.temp_f for h in hours)


def evaluate_pour(
    pour_time: datetime,
    hourly: Sequence[WeatherHour],
    mix: MixDesign,
    concrete_temp_f: Optional[float] = None,
) -> dict:
    if not hourly:
        raise ValueError("No hourly weather data available for this pour window.")

    pour_time = _floor_hour(pour_time)
    idx = _hour_index(hourly, pour_time)
    pour_hour = hourly[idx]
    window_24 = [h for h in hourly[idx:] if h.time < pour_time + timedelta(hours=24)]
    window_48 = [h for h in hourly[idx:] if h.time < pour_time + timedelta(hours=48)]
    rain_window = hourly[idx : idx + 3]

    conc_temp = assumed_concrete_temp_f(pour_hour.temp_f, concrete_temp_f)
    evap = evaporation_rate_lb_ft2_hr(
        concrete_temp_f=conc_temp,
        air_temp_f=pour_hour.temp_f,
        rh_pct=pour_hour.rh_pct,
        wind_mph=pour_hour.wind_mph,
    )

    hours_to_500 = hours_to_maturity(idx, hourly, MATURITY_500_PSI[mix.cement_type])
    hours_to_70 = hours_to_maturity(idx, hourly, MATURITY_70_PCT[mix.cement_type])

    freeze_horizon = window_24
    if hours_to_500 is not None:
        freeze_horizon = [
            h for h in hourly[idx:] if h.time < pour_time + timedelta(hours=hours_to_500)
        ] or window_24

    min_until_500 = _min_temp(freeze_horizon)
    min_24 = _min_temp(window_24)
    min_48 = _min_temp(window_48)
    rain_at_pour = max((h.precip_in for h in rain_window), default=0.0)
    precip_now = pour_hour.precip_in

    codes: list[str] = []

    if evap >= EVAP_NOGO_LB_FT2_HR:
        codes.append("EXTREME_EVAPORATION_RATE")
    elif evap >= EVAP_WARNING_LB_FT2_HR:
        codes.append("HIGH_EVAPORATION_RATE")

    if min_until_500 is not None and min_until_500 < FREEZING_F:
        codes.append("FREEZING_BEFORE_500_PSI")
    elif min_48 is not None and min_48 < COLD_WEATHER_F:
        codes.append("COLD_WEATHER_CONDITIONS")

    if rain_at_pour >= HEAVY_RAIN_IN:
        codes.append("HEAVY_RAIN_DURING_POUR")
    elif rain_at_pour >= RAIN_WARNING_IN:
        codes.append("RAIN_DURING_POUR")

    if pour_hour.temp_f >= HIGH_AMBIENT_F:
        codes.append("HIGH_AMBIENT_TEMPERATURE")

    if pour_hour.wind_mph >= HIGH_WIND_MPH and "HIGH_EVAPORATION_RATE" not in codes and "EXTREME_EVAPORATION_RATE" not in codes:
        codes.append("HIGH_WIND")

    nogo = {"EXTREME_EVAPORATION_RATE", "FREEZING_BEFORE_500_PSI", "HEAVY_RAIN_DURING_POUR"}
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

    days_to_70 = round(hours_to_70 / 24.0, 1) if hours_to_70 is not None else None
    hours_to_500_int = int(round(hours_to_500)) if hours_to_500 is not None else None

    chart_hours: list[HourlyPoint] = []
    for hour in window_48:
        hour_conc = assumed_concrete_temp_f(hour.temp_f, concrete_temp_f)
        chart_hours.append(
            HourlyPoint(
                time=hour.time.isoformat(),
                temp_f=round(hour.temp_f, 1),
                relative_humidity_pct=round(hour.rh_pct, 1),
                wind_speed_mph=round(hour.wind_mph, 1),
                precipitation_in=round(hour.precip_in, 3),
                evaporation_rate_lbs_sqft_hr=evaporation_rate_lb_ft2_hr(
                    concrete_temp_f=hour_conc,
                    air_temp_f=hour.temp_f,
                    rh_pct=hour.rh_pct,
                    wind_mph=hour.wind_mph,
                ),
            )
        )

    return {
        "go_no_go_status": status,
        "risk_factors": codes,
        "risk_factor_details": details,
        "metrics": Metrics(
            ambient_temp_f=round(pour_hour.temp_f, 1),
            relative_humidity_pct=round(pour_hour.rh_pct, 1),
            wind_speed_mph=round(pour_hour.wind_mph, 1),
            precipitation_in=round(precip_now, 3),
            concrete_temp_f=round(conc_temp, 1),
            calculated_evaporation_rate_lbs_sqft_hr=evap,
            min_temp_next_24h_f=round(min_24, 1) if min_24 is not None else None,
            min_temp_next_48h_f=round(min_48, 1) if min_48 is not None else None,
        ),
        "predictions": Predictions(
            estimated_time_to_500_psi_hours=hours_to_500_int,
            estimated_days_to_70_percent_strength=days_to_70,
        ),
        "recommended_mitigation": mitigation,
        "hourly": chart_hours,
    }
