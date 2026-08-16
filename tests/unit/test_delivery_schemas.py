import pytest
from pydantic import ValidationError

from app.schemas.customers import CustomerUpdate
from app.schemas.delivery import ZoneCreate


def test_customer_update_rejects_null_required_field() -> None:
    with pytest.raises(ValidationError, match="Fields cannot be null: address"):
        CustomerUpdate(address=None)


def test_zone_rejects_malformed_polygon_boundary() -> None:
    with pytest.raises(ValidationError, match="outer ring must be closed"):
        ZoneCreate(
            name="Johar Town",
            boundary={
                "type": "Polygon",
                "coordinates": [[[74.2, 31.4], [74.3, 31.4], [74.3, 31.5], [74.2, 31.5]]],
            },
        )


def test_zone_accepts_closed_polygon_boundary() -> None:
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[74.2, 31.4], [74.3, 31.4], [74.3, 31.5], [74.2, 31.4]]
        ],
    }
    assert ZoneCreate(name="Johar Town", boundary=boundary).boundary == boundary
