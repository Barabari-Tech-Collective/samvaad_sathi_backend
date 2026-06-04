import datetime
import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column, relationship
from sqlalchemy.sql import functions as sqlalchemy_functions
from src.repository.table import Base

class UserResumeInstance(Base):
    __tablename__ = "user_resume_instances"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    
    # Matching exact user id type (int) and cascade rules
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    
    template_id: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False, server_default="default_ats_001"
    )
    
    # Using JSONB for performance and matching existing skills column format
    resume_data: SQLAlchemyMapped[dict] = sqlalchemy_mapped_column(JSONB, nullable=False)
    
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=False, server_default="DRAFT"
    )
    
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), 
        nullable=False, 
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now()
    )

    __mapper_args__ = {"eager_defaults": True}