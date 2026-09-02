from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, require_roles
from app.core.database import get_db
from app.models.alert import Alert
from app.models.user import Role
from app.schemas.alert import AlertCreate, AlertRead

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertRead])
def list_alerts(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    trip_id: int | None = Query(default=None),
    only_unresolved: bool = Query(default=False),
) -> list[Alert]:
    """Lista las alertas del colegio (retrasos/anomalías detectadas por el
    módulo de IA, más las que reporte manualmente un monitor). Cualquier
    usuario autenticado del colegio puede verlas — resolverlas sí requiere
    rol ADMIN o MONITOR (ver abajo)."""
    query = db.query(Alert).filter(Alert.tenant_id == tenant_id)
    if trip_id is not None:
        query = query.filter(Alert.trip_id == trip_id)
    if only_unresolved:
        query = query.filter(Alert.resolved_at.is_(None))
    return query.order_by(Alert.created_at.desc()).all()


@router.post("", response_model=AlertRead, status_code=status.HTTP_201_CREATED)
def create_alert(
    payload: AlertCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _staff=Depends(require_roles(Role.ADMIN, Role.MONITOR)),
) -> Alert:
    """Reporta una incidencia a mano desde la app móvil del MONITOR (o desde
    el panel de ADMIN): 'alumno ausente' o 'problema durante el viaje'. A
    diferencia de las alertas de DELAY/ANOMALY/GEOFENCE_EXIT (que solo crea el
    módulo de IA en `app/ws/gps.py`), esta es la única forma en que una alerta
    nace de una acción humana en vez de un modelo."""
    alert = Alert(tenant_id=tenant_id, **payload.model_dump())
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/{alert_id}/resolve", response_model=AlertRead)
def resolve_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _staff=Depends(require_roles(Role.ADMIN, Role.MONITOR)),
) -> Alert:
    alert = db.query(Alert).filter(Alert.id == alert_id, Alert.tenant_id == tenant_id).first()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerta no encontrada")
    alert.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(alert)
    return alert
