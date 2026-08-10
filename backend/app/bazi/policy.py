"""Versioned calculation policies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CalculationPolicy(BaseModel):
    """The explicit assumptions used by the chart engine."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["v2"] = "v2"
    year_boundary: Literal["lichun"] = "lichun"
    month_boundary: Literal["solar_terms"] = "solar_terms"
    day_boundary: Literal["zi_hour_start"] = "zi_hour_start"
    time_basis: Literal["beijing_standard_time"] = "beijing_standard_time"
    true_solar_time: bool = True


DEFAULT_POLICY = CalculationPolicy()
