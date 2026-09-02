"""alertas: columna student_id para incidencias por estudiante

Revision ID: 5fbfde43bf16
Revises: 70654008bd0e
Create Date: 2026-09-02 03:05:12.545324

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5fbfde43bf16'
down_revision: Union[str, None] = '70654008bd0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nota: el autogenerate de Alembic también proponía `op.drop_table('spatial_ref_sys')`
    # -- es una tabla interna de PostGIS (no nuestra), se quitó a mano de este
    # archivo para no borrarla por error.
    op.add_column('alerts', sa.Column('student_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_alerts_student_id'), 'alerts', ['student_id'], unique=False)
    op.create_foreign_key(None, 'alerts', 'students', ['student_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(None, 'alerts', type_='foreignkey')
    op.drop_index(op.f('ix_alerts_student_id'), table_name='alerts')
    op.drop_column('alerts', 'student_id')
