from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, get_current_user, require_roles
from app.core.database import get_db
from app.models.student import Student
from app.models.user import Role, User
from app.schemas.student import StudentCreate, StudentRead, StudentUpdate

router = APIRouter(prefix="/students", tags=["students"])


def _get_owned_student(db: Session, tenant_id: int, student_id: int) -> Student:
    student = (
        db.query(Student)
        .filter(Student.id == student_id, Student.tenant_id == tenant_id)
        .first()
    )
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")
    return student


@router.get("", response_model=list[StudentRead])
def list_students(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    current_user: User = Depends(get_current_user),
) -> list[Student]:
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
    return query.all()


@router.post("", response_model=StudentRead, status_code=status.HTTP_201_CREATED)
def create_student(
    payload: StudentCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> Student:
    student = Student(tenant_id=tenant_id, **payload.model_dump())
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


@router.put("/{student_id}", response_model=StudentRead)
def update_student(
    student_id: int,
    payload: StudentUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> Student:
    student = _get_owned_student(db, tenant_id, student_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(student, field, value)
    db.commit()
    db.refresh(student)
    return student


@router.delete("/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> None:
    student = _get_owned_student(db, tenant_id, student_id)
    db.delete(student)
    db.commit()
