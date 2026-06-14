from sqlalchemy import Column, Integer, String, Text, JSON
from src.repository.table import Base


class AIResumeAnalysis(Base):
    __tablename__ = "ai_resume_analyses"

    id = Column(Integer, primary_key=True)
    analysis_id = Column(String, unique=True, nullable=False)

    user_id = Column(Integer, nullable=False)

    target_role = Column(String)
    experience_level = Column(String)

    job_description = Column(Text)
    extracted_resume_text = Column(Text)

    analysis_result = Column(JSON)