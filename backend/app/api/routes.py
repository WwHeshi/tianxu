"""Versioned HTTP routes."""

from fastapi import APIRouter, HTTPException, status

from ..bazi.engine import ENGINE_VERSION, ChartCalculationError, calculate_chart
from ..schemas import BirthInput, ChartPreviewResponse, HealthResponse

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="bazi-backend", engine_version=ENGINE_VERSION)


@router.post(
    "/charts/preview",
    response_model=ChartPreviewResponse,
    status_code=status.HTTP_200_OK,
    tags=["charts"],
)
def preview_chart(payload: BirthInput) -> ChartPreviewResponse:
    try:
        return calculate_chart(payload)
    except ChartCalculationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
