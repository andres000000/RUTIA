from pydantic import BaseModel


class StudentCreate(BaseModel):
    full_name: str
    grade: str | None = None
    parent_id: int | None = None
    route_id: int | None = None
    stop_id: int | None = None


class StudentUpdate(BaseModel):
    full_name: str | None = None
    grade: str | None = None
    parent_id: int | None = None
    route_id: int | None = None
    stop_id: int | None = None
    is_active: bool | None = None


class StudentRead(BaseModel):
    id: int
    tenant_id: int
    full_name: str
    grade: str | None = None
    parent_id: int | None = None
    route_id: int | None = None
    stop_id: int | None = None
    is_active: bool

    model_config = {"from_attributes": True}
