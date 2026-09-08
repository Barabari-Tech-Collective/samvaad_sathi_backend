import datetime

import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column, relationship
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class UserResume(Base):  # type: ignore
    __tablename__ = "user_resume"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    s3_key: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=512), nullable=False)
    filename: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=256), nullable=False)
    resume_text: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=True)
    source: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=32), nullable=False)
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )
    content_type: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=128), nullable=True)
    size_bytes: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.Integer, nullable=True)
    file_sha256: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=64), nullable=True)

    user = relationship("User", back_populates="resumes")

    __table_args__ = (
        sqlalchemy.UniqueConstraint("user_id", "file_sha256", name="uq_user_resume_user_sha256"),
        sqlalchemy.CheckConstraint(
            "source IN ('onboarding', 'ats_final')",
            name="chk_user_resume_source"
        ),
        sqlalchemy.Index("ix_user_resume_user_id_created_at", "user_id", "created_at"),
    )
    __mapper_args__ = {"eager_defaults": True}
