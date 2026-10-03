from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import to_shape
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, get_current_user, require_roles
from app.api.v1.routes import _get_owned_route, _ordered_stops, _point, _refresh_route_path
from app.core.database import get_db
from app.models.route import Route, Stop
from app.models.student import Student
from app.models.user import Role, User
from app.schemas.route import PointIn
from app.schemas.student import StudentCreate, StudentRead, StudentUpdate

router = APIRouter(prefix="/students", tags=["students"])

# Campos de la API que no son columnas de Student (se procesan aparte).
_NON_COLUMN_FIELDS = {"home_location"}


def _get_owned_student(db: Session, tenant_id: int, student_id: int) -> Student:
    student = (
        db.query(Student)
        .filter(Student.id == student_id, Student.tenant_id == tenant_id)
        .first()
    )
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")
    return student


def _home_stop(db: Session, student: Student) -> Stop | None:
    return db.query(Stop).filter(Stop.student_id == student.id, Stop.tenant_id == student.tenant_id).first()


def _home_stop_name(student: Student) -> str:
    """Nombre visible de la parada de casa: así el conductor y el monitor ven
    a quién recogen ahí, y la dirección para ubicarse."""
    label = f"{student.full_name} — {student.address}" if student.address else f"Casa de {student.full_name}"
    return label[:150]


def _place_before_school(db: Session, tenant_id: int, route_id: int, stop: Stop) -> None:
    """Ubica `stop` justo antes de la última parada de la ruta (el colegio),
    igual que hace el panel al agregar una parada desde el mapa."""
    others = [s for s in _ordered_stops(db, tenant_id, route_id) if s.id != stop.id]
    ordered = others[:-1] + [stop] + others[-1:] if others else [stop]
    for index, s in enumerate(ordered, start=1):
        s.order_index = index


def _sync_home_stop(db: Session, student: Student, location: PointIn | None) -> None:
    """Mantiene la parada de casa del estudiante de acuerdo con su ruta y su
    dirección: la crea, la mueve de ruta, la reubica o la borra, y recalcula el
    trazado de las rutas afectadas.

    - Sin ruta: se borra la parada de casa (no tendría a qué recorrido pertenecer).
    - Con ruta y ubicación: se crea o actualiza, y queda como su parada asignada.
    - Con ruta pero sin ubicación ni parada de casa previa: no se toca nada (el
      estudiante puede seguir usando una parada compartida, como en el seed).
    """
    home = _home_stop(db, student)
    touched_route_ids: set[int] = set()

    if student.route_id is None:
        if home is not None:
            touched_route_ids.add(home.route_id)
            db.delete(home)
            student.stop_id = None
    elif location is not None or home is not None:
        if home is None:
            home = Stop(
                tenant_id=student.tenant_id,
                route_id=student.route_id,
                name=_home_stop_name(student),
                order_index=0,
                geom=_point(location),
                student_id=student.id,
            )
            db.add(home)
            db.flush()
            _place_before_school(db, student.tenant_id, student.route_id, home)
        else:
            if home.route_id != student.route_id:
                touched_route_ids.add(home.route_id)
                home.route_id = student.route_id
                db.flush()
                _place_before_school(db, student.tenant_id, student.route_id, home)
            if location is not None:
                home.geom = _point(location)
            home.name = _home_stop_name(student)
        student.stop_id = home.id
        touched_route_ids.add(student.route_id)

    db.flush()
    for route_id in touched_route_ids:
        route = db.get(Route, route_id)
        if route is not None:
            _refresh_route_path(db, route)


def _to_read(student: Student, home: Stop | None) -> StudentRead:
    point = to_shape(home.geom) if home is not None else None
    return StudentRead(
        id=student.id,
        tenant_id=student.tenant_id,
        full_name=student.full_name,
        grade=student.grade,
        parent_id=student.parent_id,
        route_id=student.route_id,
        stop_id=student.stop_id,
        is_active=student.is_active,
        address=student.address,
        home_lat=point.y if point else None,
        home_lon=point.x if point else None,
    )


def _validate_route(db: Session, tenant_id: int, route_id: int | None) -> None:
    if route_id is not None:
        _get_owned_route(db, tenant_id, route_id)  # 404 si es de otro colegio


@router.get("", response_model=list[StudentRead])
def list_students(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    current_user: User = Depends(get_current_user),
) -> list[StudentRead]:
    """
    Cualquier usuario del colegio puede listar estudiantes (lo usan el ADMIN,
    el MONITOR -- para saber quién va en el viaje que está acompañando -- y
    el PADRE -- para su pantalla "Mis hijos"). Un PADRE, sin embargo, solo
    debe ver a SUS propios hijos, nunca el listado completo del colegio: eso
    sería exponer los nombres/grados de estudiantes ajenos a un acudiente que
    no tiene ninguna relación con ellos.
    """
    query = db.query(Student).filter(Student.tenant_id == tenant_id)
    if current_user.role == Role.PADRE:
        query = query.filter(Student.parent_id == current_user.id)
    students = query.all()
    # Una sola consulta para todas las paradas de casa (en vez de una por estudiante).
    homes = {
        s.student_id: s
        for s in db.query(Stop).filter(
            Stop.tenant_id == tenant_id, Stop.student_id.in_([st.id for st in students])
        )
    } if students else {}
    return [_to_read(s, homes.get(s.id)) for s in students]


@router.post("", response_model=StudentRead, status_code=status.HTTP_201_CREATED)
def create_student(
    payload: StudentCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> StudentRead:
    _validate_route(db, tenant_id, payload.route_id)
    student = Student(tenant_id=tenant_id, **payload.model_dump(exclude=_NON_COLUMN_FIELDS))
    db.add(student)
    db.flush()  # para tener student.id antes de crear su parada de casa
    _sync_home_stop(db, student, payload.home_location)
    db.commit()
    db.refresh(student)
    return _to_read(student, _home_stop(db, student))


@router.put("/{student_id}", response_model=StudentRead)
def update_student(
    student_id: int,
    payload: StudentUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> StudentRead:
    student = _get_owned_student(db, tenant_id, student_id)
    changes = payload.model_dump(exclude_unset=True, exclude=_NON_COLUMN_FIELDS)
    if "route_id" in changes:
        _validate_route(db, tenant_id, changes["route_id"])
    for field, value in changes.items():
        setattr(student, field, value)
    # Solo se toca la parada de casa si cambió algo que la afecta (así editar
    # el grado, por ejemplo, no recalcula el recorrido de la ruta).
    if payload.home_location is not None or {"route_id", "address", "full_name"} & changes.keys():
        _sync_home_stop(db, student, payload.home_location)
    db.commit()
    db.refresh(student)
    return _to_read(student, _home_stop(db, student))


@router.delete("/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> None:
    """Borra también su parada de casa y recalcula el recorrido de la ruta."""
    student = _get_owned_student(db, tenant_id, student_id)
    home = _home_stop(db, student)
    route = db.get(Route, home.route_id) if home is not None else None
    if home is not None:
        student.stop_id = None
        db.delete(home)
        db.flush()
    db.delete(student)
    db.flush()
    if route is not None:
        _refresh_route_path(db, route)
    db.commit()
