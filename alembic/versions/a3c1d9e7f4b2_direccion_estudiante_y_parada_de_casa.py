"""direccion del estudiante y parada de casa (stops.student_id)

Revision ID: a3c1d9e7f4b2
Revises: 5fbfde43bf16
Create Date: 2026-10-02

Solo agrega dos columnas NULL (sin tocar datos existentes): es seguro de
aplicar sobre la base de la demo en Railway.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c1d9e7f4b2"
down_revision: Union[str, None] = "5fbfde43bf16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("students", sa.Column("address", sa.String(length=255), nullable=True))
    op.add_column("stops", sa.Column("student_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_stops_student_id"), "stops", ["student_id"], unique=False)
    op.create_foreign_key(
        "fk_stops_student_id_students", "stops", "students", ["student_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_stops_student_id_students", "stops", type_="foreignkey")
    op.drop_index(op.f("ix_stops_student_id"), table_name="stops")
    op.drop_column("stops", "student_id")
    op.drop_column("students", "address")
