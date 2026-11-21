from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from marketpulse.alerts.notify import DeliveryError, Notifier
from marketpulse.alerts.store import AlertStore
from marketpulse.api.problems import ProblemError
from marketpulse.market.windows import Window

router = APIRouter(prefix="/alerts", tags=["alerts"])


class RuleIn(BaseModel):
    symbol: str
    window: Window
    threshold_pct: Annotated[Decimal, Field(gt=0, le=100, max_digits=8, decimal_places=4)]
    cooldown_minutes: Annotated[int, Field(ge=1, le=1_440)] = 60


class RuleOut(RuleIn):
    id: int


class NotificationOut(BaseModel):
    id: int
    kind: str
    rule_id: int | None
    text: str
    status: str
    attempts: int
    error: str | None
    created_at: datetime
    sent_at: datetime | None


class TestResult(BaseModel):
    channel: str
    attempts: int


def store(request: Request) -> AlertStore:
    return AlertStore(request.app.state.engine)


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(request: Request) -> list[RuleOut]:
    return [RuleOut(**vars(rule)) for rule in await store(request).rules()]


@router.post("/rules", response_model=RuleOut, status_code=201)
async def add_rule(request: Request, body: RuleIn) -> RuleOut:
    symbol = body.symbol.upper().replace("/", "")
    tracked = request.app.state.settings.symbols
    if symbol not in tracked:
        raise ProblemError(422, f"{body.symbol} is not tracked. Tracked: {', '.join(tracked)}.")
    rule = await store(request).add_rule(
        symbol, body.window, body.threshold_pct, body.cooldown_minutes
    )
    return RuleOut(**vars(rule))


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(request: Request, rule_id: int) -> Response:
    if not await store(request).delete_rule(rule_id):
        raise ProblemError(404, f"No alert rule {rule_id}.")
    return Response(status_code=204)


@router.get("/notifications", response_model=list[NotificationOut])
async def recent_notifications(
    request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[NotificationOut]:
    return [NotificationOut(**vars(n)) for n in await store(request).recent(limit)]


@router.post("/test", response_model=TestResult)
async def send_test(request: Request) -> TestResult:
    """Sends one message through the configured channel, to check the setup."""
    notifier: Notifier = request.app.state.notifier
    try:
        attempts = await notifier.send("MarketPulse test message: alerts are set up.")
    except DeliveryError as error:
        raise ProblemError(502, f"The message was not delivered: {error}") from error
    return TestResult(channel=notifier.name, attempts=attempts)
