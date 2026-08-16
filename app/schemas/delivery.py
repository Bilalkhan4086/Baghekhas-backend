"""Protected administration contracts for zones and riders."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import APIModel


class ZoneCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    boundary: dict[str, object] | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Zone name is required")
        return value

    @field_validator("boundary")
    @classmethod
    def validate_boundary(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        return validate_polygon_boundary(value)


class ZoneUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    boundary: dict[str, object] | None = None

    @field_validator("boundary")
    @classmethod
    def validate_boundary(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        return validate_polygon_boundary(value)


def validate_polygon_boundary(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    if value.get("type") != "Polygon":
        raise ValueError("Boundary must be a GeoJSON Polygon")
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("Polygon coordinates require an outer ring")
    ring = coordinates[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("Polygon outer ring requires at least four positions")
    normalized: list[list[float]] = []
    for position in ring:
        if not isinstance(position, list) or len(position) < 2:
            raise ValueError("Polygon positions must be longitude/latitude pairs")
        longitude, latitude = position[:2]
        if (
            not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            raise ValueError("Polygon coordinates are outside valid longitude/latitude ranges")
        normalized.append([float(longitude), float(latitude)])
    if normalized[0] != normalized[-1]:
        raise ValueError("Polygon outer ring must be closed")
    return value


class ZoneResponse(APIModel):
    id: uuid.UUID
    name: str
    description: str | None
    boundary: dict[str, object] | None


class RiderCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=12, max_length=500)
    is_active: bool = True


class RiderUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, min_length=1, max_length=30)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=500)


class RiderZoneCreate(APIModel):
    zone_ids: list[uuid.UUID] = Field(min_length=1)


class RiderResponse(APIModel):
    id: uuid.UUID
    name: str
    phone: str
    is_active: bool
    created_at: datetime
    zone_ids: list[uuid.UUID] = Field(default_factory=list)
