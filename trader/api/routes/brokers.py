"""
Broker API Routes
=================
Broker account and status management endpoints.
"""
from fastapi import APIRouter, HTTPException, Path

from trader.api.models.schemas import BrokerAccount, BrokerStatus
from trader.services import BrokerService

router = APIRouter(tags=["Brokers"])


@router.get("/v1/brokers", response_model=list[BrokerAccount])
async def list_brokers():
    """
    List broker accounts.

    Returns a list of all registered broker accounts.
    """
    service = BrokerService()
    return service.list_brokers()


@router.get("/v1/brokers/{account_id}/status", response_model=BrokerStatus)
async def get_broker_status(account_id: str = Path(..., description="Account ID")):
    """
    Get broker connection status.

    Returns the connection status for a broker account.
    """
    service = BrokerService()
    status = service.get_status(account_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Broker account {account_id} not found")
    return status
