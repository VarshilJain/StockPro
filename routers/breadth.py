"""
routers/breadth.py — Market Breadth EMA Percentages & Regimes API.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from auth import get_current_user
import market_breadth_service as mbs

router = APIRouter(prefix="/api/breadth", tags=["Market Breadth"])


@router.get("/ema")
def get_ema_market_breadth(
    universe: str = Query("all", description="Universe: 'all', 'nifty500', or 'nifty50'"),
    months: int = Query(12, ge=1, le=36, description="Display window in months"),
    refresh: bool = Query(False, description="Force recompute bypassing cache"),
    current_user: dict = Depends(get_current_user),
):
    """
    Returns the market breadth data:
    - % of stocks trading above EMA 10, 20, 50, 100, 150, 200
    - Paired series (10/20, 50/100, 150/200)
    - Thresholds: 75 (overbought), 50 (median), 25 (oversold)
    - Signals: overbought, oversold, cross-up reversals, cross-down tops
    - Latest KPI summary cards
    """
    try:
        payload = mbs.get_market_breadth_payload(
            universe=universe,
            display_months=months,
            force_refresh=refresh,
        )
        return JSONResponse(content=payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Market breadth error: {str(exc)}")


@router.post("/recalculate")
def recalculate_market_breadth(
    background_tasks: BackgroundTasks,
    universe: Optional[str] = "all",
    current_user: dict = Depends(get_current_user),
):
    """Trigger asynchronous recalculation and backfill of market breadth."""
    def _run_backfill():
        if universe == "all":
            mbs.backfill_all_universes()
        else:
            mbs.calculate_market_breadth_df(universe=universe, display_months=18)

    background_tasks.add_task(_run_backfill)
    return {"status": "ok", "message": f"Recalculation started for universe: {universe}"}
