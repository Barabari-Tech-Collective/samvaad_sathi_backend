from __future__ import annotations
import datetime
import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions
from sqlalchemy.dialects.postgresql import JSONB
from src.repository.table import Base

class JobProfile(Base):
    __tablename__ = "job_profile"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    job_name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=160), nullable=False, index=True)
    job_description: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=False)
    experience_level: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=64), nullable=True)
    company_name: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=256), nullable=True)
    skills: SQLAlchemyMapped[list[str] | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    additional_context: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=True)
    
    created_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now(), index=True
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )

    __mapper_args__ = {"eager_defaults": True}

    @property
    def title(self) -> str:
        return self.job_name
    
    @title.setter
    def title(self, value: str):
        self.job_name = value

    @property
    def description(self) -> str | None:
        return self.job_description
    
    @description.setter
    def description(self, value: str | None):
        self.job_description = value or ""
