import datetime
import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column, relationship
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class TeamMember(Base):  # type: ignore
    __tablename__ = "team_member"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    team_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, sqlalchemy.ForeignKey("team.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    
    access_role: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=128), nullable=False)
    
    purposes: SQLAlchemyMapped[list | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    domain: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=128), nullable=True)
    course_ids: SQLAlchemyMapped[list | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    colleges: SQLAlchemyMapped[list | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=False, server_default=sqlalchemy.text("'active'")
    )
    removed_date: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=True
    )

    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )

    team = relationship("Team", back_populates="members")
    # Using string reference for User to avoid circular imports if any, or directly "User" if imported
    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        sqlalchemy.UniqueConstraint("team_id", "user_id", name="uq_team_member_team_id_user_id"),
    )

    __mapper_args__ = {"eager_defaults": True}
