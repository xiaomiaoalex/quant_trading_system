"""
Health API Routes
================
Health check endpoints for the Systematic Trader Control Plane API.
"""
from fastapi import APIRouter

from trader.api.models.schemas import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.

    Returns the current health status of the API.
    """
    return HealthResponse()
