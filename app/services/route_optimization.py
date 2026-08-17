"""Road-network route optimization behind a testable provider boundary."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from google.maps import routeoptimization_v1

from app.exceptions import DomainError


@dataclass(frozen=True)
class OptimizationStop:
    order_id: uuid.UUID
    latitude: Decimal
    longitude: Decimal


@dataclass(frozen=True)
class OptimizedLeg:
    order_id: uuid.UUID
    distance_meters: int
    duration_seconds: int


@dataclass(frozen=True)
class OptimizedRoute:
    legs: list[OptimizedLeg]
    total_distance_meters: int
    total_duration_seconds: int


class RouteOptimizer(Protocol):
    async def optimize(
        self,
        *,
        delivery_date: date,
        start_latitude: Decimal,
        start_longitude: Decimal,
        stops: list[OptimizationStop],
    ) -> OptimizedRoute: ...


class GoogleRouteOptimizer:
    def __init__(self, project_id: str, timeout_seconds: int) -> None:
        self.project_id = project_id
        self.timeout_seconds = timeout_seconds

    async def optimize(
        self,
        *,
        delivery_date: date,
        start_latitude: Decimal,
        start_longitude: Decimal,
        stops: list[OptimizationStop],
    ) -> OptimizedRoute:
        if not stops:
            raise DomainError(409, "no_route_deliveries", "No deliveries are ready for routing")

        start_at = datetime.combine(
            delivery_date, time(6), tzinfo=ZoneInfo("Asia/Karachi")
        ).astimezone(UTC)
        end_at = start_at + timedelta(hours=18)
        request = {
            "parent": f"projects/{self.project_id}",
            "timeout": f"{self.timeout_seconds}s",
            "model": {
                "global_start_time": start_at,
                "global_end_time": end_at,
                "shipments": [
                    {
                        "label": str(stop.order_id),
                        "deliveries": [
                            {
                                "arrival_location": {
                                    "latitude": float(stop.latitude),
                                    "longitude": float(stop.longitude),
                                }
                            }
                        ],
                    }
                    for stop in stops
                ],
                "vehicles": [
                    {
                        "label": "rider",
                        "start_location": {
                            "latitude": float(start_latitude),
                            "longitude": float(start_longitude),
                        },
                        "cost_per_traveled_hour": 1.0,
                    }
                ],
            },
        }
        try:
            client = routeoptimization_v1.RouteOptimizationAsyncClient()
            response = await client.optimize_tours(
                request=request,
                timeout=float(self.timeout_seconds + 2),
            )
        except Exception as error:
            raise DomainError(
                503,
                "route_optimization_unavailable",
                "Route optimization is temporarily unavailable",
            ) from error

        if response.validation_errors or response.skipped_shipments or len(response.routes) != 1:
            raise DomainError(
                422,
                "route_optimization_incomplete",
                "The route provider could not include every delivery",
            )

        route = response.routes[0]
        input_ids = [stop.order_id for stop in stops]
        legs: list[OptimizedLeg] = []
        for index, visit in enumerate(route.visits):
            shipment_index = int(visit.shipment_index)
            if shipment_index < 0 or shipment_index >= len(input_ids):
                raise DomainError(
                    502,
                    "route_optimization_invalid",
                    "The route provider returned an invalid delivery sequence",
                )
            transition = route.transitions[index]
            legs.append(
                OptimizedLeg(
                    order_id=input_ids[shipment_index],
                    distance_meters=max(0, int(transition.travel_distance_meters)),
                    duration_seconds=max(0, int(transition.travel_duration.seconds)),
                )
            )

        if len(legs) != len(stops) or len({leg.order_id for leg in legs}) != len(stops):
            raise DomainError(
                502,
                "route_optimization_invalid",
                "The route provider returned an invalid delivery sequence",
            )
        return OptimizedRoute(
            legs=legs,
            total_distance_meters=sum(leg.distance_meters for leg in legs),
            total_duration_seconds=sum(leg.duration_seconds for leg in legs),
        )
