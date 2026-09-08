import datetime

import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column, relationship
from sqlalchemy.sql import functions as sqlalchemy_functions
from sqlalchemy import String
from src.repository.table import Base

# ----------------------------------
# Enum for preferred target position
# ----------------------------------
from enum import Enum as _Enum


class TargetPositionEnum(str, _Enum):
    """Enumeration for the user's desired target position."""

    DATA_SCIENCE = "Data Science"
    FRONTEND_DEVELOPMENT = "Frontend Development"
    BACKEND_DEVELOPMENT = "Backend Development"
    SOFTWARE_DEVELOPMENT = "Software Development"


class User(Base):  # type: ignore
    __tablename__ = "user"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    student_id: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
    sqlalchemy.String(length=128),
    nullable=True,
    unique=True,
    doc="Unique student identifier provided by Sampark Saathi"
    )
    email: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=254), nullable=False, unique=True, index=True
    )
    password_hash: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=1024), nullable=False)
    name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=128), nullable=False)
    # Optional resume fields
    resume_text: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=True)
    original_resume_s3_key: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=512), nullable=True
    )

    # --- New profile attributes ---
    degree: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=128), nullable=True, doc="UG/PG degree such as B.Tech, M.Sc., etc."
    )
    university: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=256), nullable=True, doc="Name of University / College"
    )

    # Store raw image bytes (≤5 MB recommended). In Postgres this maps to BYTEA.
    profile_picture: SQLAlchemyMapped[bytes | None] = sqlalchemy_mapped_column(sqlalchemy.LargeBinary, nullable=True)

    # Enum column for target position preferences
    target_position: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        String,
        nullable=True,
    )

    # Years of experience (stored as float for partial years)
    years_experience: SQLAlchemyMapped[float | None] = sqlalchemy_mapped_column(sqlalchemy.Float, nullable=True)

    company: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=256), nullable=True)

    # JSON skills (retained)
    skills: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    
    # Onboarding completion flag
    is_onboarded: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean, nullable=False, server_default=sqlalchemy.text("false"),
        doc="Indicates whether the user has completed the onboarding flow via /api/users/profile"
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )

    sessions = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    interviews = relationship(
        "Interview", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    resumes = relationship(
        "UserResume", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    __mapper_args__ = {"eager_defaults": True}


