from pydantic import BaseModel, Field


class VehicleCreate(BaseModel):
    plate: str = Field(..., min_length=3, max_length=20)
    capacity: int = Field(20, ge=1, le=100)
    driver_id: int | None = None
    monitor_id: int | None = None


class VehicleUpdate(BaseModel):
    plate: str | None = None
    capacity: int | None = None
    driver_id: int | None = None
    monitor_id: int | None = None
    is_active: bool | None = None


class VehicleRead(BaseModel):
    id: int
    tenant_id: int
    plate: str
    capacity: int
    driver_id: int | None = None
    monitor_id: int | None = None
    is_active: bool

    model_config = {"from_attributes": True}
