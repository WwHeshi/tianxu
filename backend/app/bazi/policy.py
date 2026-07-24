"""Versioned calculation policies.

The policy is deliberately narrow in v1.  Expanding a rule should create a new
policy version instead of silently changing existing chart results.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CalculationPolicy(BaseModel):
    """The explicit assumptions used by the chart engine."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"] = "v1"
    year_boundary: Literal["lichun"] = "lichun"
    month_boundary: Literal["solar_terms"] = "solar_terms"
    day_boundary: Literal["midnight"] = "midnight"
    time_basis: Literal["beijing_standard_time"] = "beijing_standard_time"
    true_solar_time: Literal[True] = True


DEFAULT_POLICY = CalculationPolicy()
