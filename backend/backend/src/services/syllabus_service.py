"""
Syllabus service for interview question generation.

This module provides a clean, object-oriented interface for managing
interview question topics, roles, and difficulty levels.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
import logging

from .syllabus_data import (
    ARCHETYPES,
    BEHAVIORAL_TOPICS,
    CANONICAL_ROLES,
    DEPTH_GUIDELINES,
    ROLE_ALIASES,
    TECH_KEYWORDS,
)
from .syllabus_content import SYLLABUS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopicBank:
    """Immutable container for topic categories."""
    tech: List[str]
    tech_allied: List[str]
    behavioral: List[str]
    archetypes: List[str]
    depth_guidelines: List[str]

    def __post_init__(self) -> None:
        """Validate the topic bank after initialization."""
        if not self.tech and not self.tech_allied and not self.behavioral:
            raise ValueError("At least one topic category must be non-empty")


@dataclass(frozen=True)
class QuestionRatio:
    """Immutable container for question distribution ratios."""
    tech: int
    tech_allied: int
    behavioral: int

    def __post_init__(self) -> None:
        """Validate the question ratio after initialization."""
        total = self.tech + self.tech_allied + self.behavioral
        if total != 5:
            raise ValueError(f"Question ratio must total 5, got {total}")
        
        if any(count < 0 for count in [self.tech, self.tech_allied, self.behavioral]):
            raise ValueError("All question counts must be non-negative")


class RoleManager:
    """Manages role mapping and validation."""
    
    def __init__(self) -> None:
        self._canonical_roles: Set[str] = set(CANONICAL_ROLES)
        self._aliases: Dict[str, str] = ROLE_ALIASES.copy()
    
    def derive_role(self, track: str, fallback_to_default: bool = True) -> Optional[str]:
        """
        Derive canonical role from track string.
        
        Args:
            track: Input track string (e.g., "react", "frontend")
            fallback_to_default: Whether to fall back to 'JavaScript Developer' if no match.
            
        Returns:
            Canonical role name or None if fallback_to_default is False and no match found
            
        Raises:
            ValueError: If track is invalid
        """
        try:
            if not track or not isinstance(track, str):
                raise ValueError("Track must be a non-empty string")
            
            normalized_track = track.strip().lower()
            if not normalized_track:
                raise ValueError("Track cannot be empty or whitespace only")

            if normalized_track.startswith("non-tech:"):
                raise ValueError(
                    f"Track '{track}' is a non-tech track and has no tech role mapping. "
                    "Non-tech interviews must use the non-tech question generation path."
                )

            # Check for exact matches first
            if normalized_track in self._aliases:
                role = self._aliases[normalized_track]
                logger.debug(f"Exact match found for track '{track}' -> '{role}'")
                return role
            
            # Check for partial matches
            for key, role in self._aliases.items():
                if key in normalized_track:
                    logger.debug(f"Partial match found for track '{track}' -> '{role}' (key: '{key}')")
                    return role
            
            # Default fallback
            if not fallback_to_default:
                logger.info(f"No role match found for track '{track}', returning None")
                return None

            logger.warning(f"No role match found for track '{track}', using default 'JavaScript Developer'")
            return "JavaScript Developer"
            
        except Exception as e:
            logger.error(f"Error deriving role for track '{track}': {e}")
            raise
    
    def is_valid_role(self, role: str) -> bool:
        """Check if a role is canonical."""
        return role in self._canonical_roles
    
    def get_all_roles(self) -> List[str]:
        """Get all canonical roles."""
        return CANONICAL_ROLES.copy()


class DifficultyManager:
    """Manages difficulty levels and validation."""
    
    VALID_DIFFICULTIES = {"easy", "medium", "hard", "expert"}
    
    @classmethod
    def normalize_difficulty(cls, difficulty: Optional[str]) -> str:
        """
        Normalize difficulty string to valid value.
        
        Args:
            difficulty: Input difficulty string
            
        Returns:
            Normalized difficulty ("easy", "medium", "hard", or "expert")
        """
        if not difficulty or not isinstance(difficulty, str):
            return "medium"
        
        normalized = difficulty.strip().lower()
        return normalized if normalized in cls.VALID_DIFFICULTIES else "medium"
    
    @classmethod
    def is_valid_difficulty(cls, difficulty: str) -> bool:
        """Check if difficulty is valid."""
        return difficulty in cls.VALID_DIFFICULTIES


class SyllabusService:
    """Main service for managing interview question syllabus."""
    
    def __init__(self) -> None:
        self._role_manager = RoleManager()
        self._difficulty_manager = DifficultyManager()
        self._syllabus = SYLLABUS.copy()
        
        # Performance optimizations: pre-compute common lookups
        self._topic_cache: Dict[str, TopicBank] = {}
        self._role_cache: Dict[str, str] = {}
        self._difficulty_cache: Dict[str, str] = {}
        
        # Pre-compute all role mappings for faster lookups
        for role in CANONICAL_ROLES:
            self._role_cache[role.lower()] = role
        for alias, role in ROLE_ALIASES.items():
            self._role_cache[alias] = role
    
    def get_topics_for_role(
        self, 
        role: Optional[str], 
        difficulty: Optional[str] = None,
        fallback_to_default: bool = False
    ) -> TopicBank:
        """
        Get topic bank for a specific role and difficulty.
        
        Args:
            role: Role name (can be alias, canonical, or None)
            difficulty: Difficulty level ("easy", "medium", "hard", "expert")
            fallback_to_default: Whether to fall back to 'JavaScript Developer' if no match.
            
        Returns:
            TopicBank containing all relevant topics
        """
        try:
            normalized_difficulty = self._difficulty_manager.normalize_difficulty(difficulty)

            if not role:
                return TopicBank(
                    tech=[],
                    tech_allied=[],
                    behavioral=list(BEHAVIORAL_TOPICS),
                    archetypes=(
                        ARCHETYPES.get("tech", []) + 
                        ARCHETYPES.get("tech_allied", []) + 
                        ARCHETYPES.get("behavioral", [])
                    ),
                    depth_guidelines=[DEPTH_GUIDELINES.get(normalized_difficulty, DEPTH_GUIDELINES["medium"])],
                )

            # Normalize inputs
            canonical_role = self._role_manager.derive_role(role, fallback_to_default=fallback_to_default)
            if not canonical_role:
                return TopicBank(
                    tech=[],
                    tech_allied=[],
                    behavioral=list(BEHAVIORAL_TOPICS),
                    archetypes=(
                        ARCHETYPES.get("tech", []) + 
                        ARCHETYPES.get("tech_allied", []) + 
                        ARCHETYPES.get("behavioral", [])
                    ),
                    depth_guidelines=[DEPTH_GUIDELINES.get(normalized_difficulty, DEPTH_GUIDELINES["medium"])],
                )

            # Check cache first for performance
            cache_key = f"{canonical_role}:{normalized_difficulty}"
            if cache_key in self._topic_cache:
                logger.debug(f"Cache hit for topics: {cache_key}")
                return self._topic_cache[cache_key]
            
            logger.debug(f"Getting topics for role '{role}' (canonical: '{canonical_role}') at difficulty '{normalized_difficulty}'")
            
            # Get role data
            role_data = self._syllabus.get(canonical_role)
            if not role_data:
                if fallback_to_default:
                    logger.warning(f"No syllabus data found for role '{canonical_role}', falling back to 'JavaScript Developer'")
                    role_data = self._syllabus.get("JavaScript Developer", {})
                else:
                    logger.info(f"No syllabus data found for role '{canonical_role}', returning empty tech topic bank")
                    return TopicBank(
                        tech=[],
                        tech_allied=[],
                        behavioral=list(BEHAVIORAL_TOPICS),
                        archetypes=(
                            ARCHETYPES.get("tech", []) + 
                            ARCHETYPES.get("tech_allied", []) + 
                            ARCHETYPES.get("behavioral", [])
                        ),
                        depth_guidelines=[DEPTH_GUIDELINES.get(normalized_difficulty, DEPTH_GUIDELINES["medium"])],
                    )
            
            # Get difficulty data
            difficulty_data = role_data.get(normalized_difficulty, {})
            if not difficulty_data:
                logger.warning(f"No data found for difficulty '{normalized_difficulty}', falling back to 'medium'")
                difficulty_data = role_data.get("medium", {})
                if not difficulty_data:
                    raise ValueError(f"No syllabus data available for difficulty '{normalized_difficulty}' or fallback")
            
            # Build topic bank
            topic_bank = TopicBank(
                tech=list(difficulty_data.get("tech", [])),
                tech_allied=list(difficulty_data.get("tech_allied", [])),
                behavioral=list(BEHAVIORAL_TOPICS),
                archetypes=(
                    ARCHETYPES.get("tech", []) + 
                    ARCHETYPES.get("tech_allied", []) + 
                    ARCHETYPES.get("behavioral", [])
                ),
                depth_guidelines=[DEPTH_GUIDELINES.get(normalized_difficulty, DEPTH_GUIDELINES["medium"])],
            )
            
            # Cache the result for future use
            self._topic_cache[cache_key] = topic_bank
            
            logger.debug(f"Successfully built topic bank with {len(topic_bank.tech)} tech, {len(topic_bank.tech_allied)} tech_allied, {len(topic_bank.behavioral)} behavioral topics")
            return topic_bank
            
        except Exception as e:
            logger.error(f"Error getting topics for role '{role}' at difficulty '{difficulty}': {e}")
            raise
    
    def compute_question_ratio(
        self,
        years_experience: Optional[float] = None,
        has_resume_text: bool = False,
        has_skills: bool = False,
    ) -> QuestionRatio:
        """
        Compute question distribution ratio based on candidate profile.
        
        Args:
            years_experience: Years of professional experience
            has_resume_text: Whether resume text is available
            has_skills: Whether skills are extracted from resume
            
        Returns:
            QuestionRatio with distribution counts
        """
        # No experience: focus on fundamentals
        if years_experience is None or years_experience < 1:
            return QuestionRatio(tech=3, tech_allied=1, behavioral=1)
        
        # Has resume but no skills: focus on behavioral assessment
        if has_resume_text and not has_skills:
            return QuestionRatio(tech=3, tech_allied=0, behavioral=2)
        
        # Standard distribution for experienced candidates
        return QuestionRatio(tech=2, tech_allied=2, behavioral=1)
    
    def extract_tech_allied_from_resume(
        self,
        resume_text: Optional[str] = None,
        skills: Optional[List[str]] = None,
        fallback_topics: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Extract tech_allied topics from resume content.
        
        Args:
            resume_text: Raw resume text
            skills: Extracted skills list
            fallback_topics: Fallback topics if extraction fails
            
        Returns:
            List of tech_allied topics
        """
        try:
            topics: List[str] = []
            seen: Set[str] = set()
            
            def add_topic(topic: str) -> None:
                """Add topic if not already seen."""
                if not topic or not isinstance(topic, str):
                    return
                normalized = topic.strip()
                if normalized and normalized.lower() not in seen:
                    seen.add(normalized.lower())
                    topics.append(normalized)
            
            # Add explicit skills first
            if skills:
                if not isinstance(skills, list):
                    logger.warning(f"Skills parameter is not a list: {type(skills)}")
                else:
                    for skill in skills:
                        add_topic(skill)
                    logger.debug(f"Added {len([s for s in skills if s and isinstance(s, str)])} skills from skills list")
            
            # Extract from resume text using keyword matching
            if resume_text and isinstance(resume_text, str):
                corpus = resume_text.lower()
                matched_keywords = []
                for keyword in TECH_KEYWORDS:
                    if keyword in corpus:
                        add_topic(keyword)
                        matched_keywords.append(keyword)
                logger.debug(f"Matched {len(matched_keywords)} keywords from resume text: {matched_keywords}")
            elif resume_text:
                logger.warning(f"Resume text is not a string: {type(resume_text)}")
            
            # Use fallback if no topics found
            if not topics and fallback_topics:
                if not isinstance(fallback_topics, list):
                    logger.warning(f"Fallback topics is not a list: {type(fallback_topics)}")
                else:
                    for topic in fallback_topics:
                        add_topic(topic)
                    logger.debug(f"Used {len(fallback_topics)} fallback topics")
            
            logger.debug(f"Extracted {len(topics)} tech_allied topics total")
            return topics
            
        except Exception as e:
            logger.error(f"Error extracting tech_allied topics from resume: {e}")
            # Return empty list as safe fallback
            return []
    
    def get_all_roles(self) -> List[str]:
        """Get all available canonical roles."""
        return self._role_manager.get_all_roles()
    
    def derive_canonical_role(self, track: str, skills: Optional[List[str]] = None) -> Optional[str]:
        """Derive canonical role if known, returning None if unknown."""
        track_str = track or ""
        track_lower = track_str.strip().lower()
        skills_text = " ".join(skills or []).lower()
        combined_text = f"{track_lower} {skills_text}"

        # If explicit other backend/language stack is detected, do not alias to MERN or JavaScript
        other_stacks = [
            "python", "django", "fastapi", "flask", "java", "spring", "springboot",
            "c++", "c#", ".net", "dotnet", "golang", "go", "ruby", "rails", "php", "rust"
        ]
        if any(stack in combined_text for stack in other_stacks):
            return None

        return self._role_manager.derive_role(track, fallback_to_default=False)

    def resolve_generation_context(
        self,
        track: str,
        category: Optional[str] = None,
        difficulty: str = "easy",
        context_text: str = "",
        skills_list: Optional[List[str]] = None,
        experience_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resolve syllabus topics, category ratios, and influence settings
        tailored for the candidate's exact track and role category.
        """
        skills = list(skills_list or [])
        track_str = track or ""
        track_lower = track_str.strip().lower()
        track_words = set(track_lower.replace("-", " ").replace("_", " ").split())
        cat_lower = (category or "").strip().lower()
        normalized_difficulty = self._difficulty_manager.normalize_difficulty(difficulty)

        is_hr = (
            cat_lower in ["hr", "human resources", "human_resources", "talent", "recruitment", "people"]
            or "hr" in track_words
            or "human resources" in track_lower
            or "recruiter" in track_words
            or "recruitment" in track_words
            or "talent" in track_words
        )
        is_sales = cat_lower in ["sales", "business development", "bde"] or "sales" in track_words or "bde" in track_words
        is_marketing = cat_lower in ["marketing", "growth"] or "marketing" in track_words
        is_data = cat_lower == "data" or "data" in track_words
        is_design = cat_lower == "design" or "design" in track_words or "ui" in track_words or "ux" in track_words
        is_general_non_tech = (
            cat_lower in ["operations", "finance", "business", "legal", "non-tech", "non_tech"]
            or track_lower.startswith("non-tech:")
        )

        is_non_tech = is_hr or is_sales or is_marketing or is_general_non_tech

        if is_non_tech:
            target_role = track_str
            topic_bank = self.get_topics_for_role(role=None, difficulty=normalized_difficulty, fallback_to_default=False)
            topics = {
                "tech": [],
                "tech_allied": skills,
                "behavioral": list(topic_bank.behavioral),
                "archetypes": [],
                "depth_guidelines": topic_bank.depth_guidelines,
            }
            if is_hr:
                ratio = {"hr_operations": 2, "talent_acquisition": 2, "behavioral": 1}
            elif is_sales:
                ratio = {"sales_strategy": 2, "client_management": 2, "behavioral": 1}
            elif is_marketing:
                ratio = {"marketing_strategy": 2, "analytics_execution": 2, "behavioral": 1}
            else:
                ratio = {"core_domain": 2, "practical_scenario": 2, "behavioral": 1}

            influence = {
                "target_role": target_role,
                "category": category,
                "difficulty": normalized_difficulty,
                "skills": skills,
                "experience_level": experience_level,
                "is_non_tech": True,
            }
            return {
                "target_role": target_role,
                "topics": topics,
                "ratio": ratio,
                "influence": influence,
                "is_non_tech": True,
            }

        # For Technical Roles:
        canonical_role = self.derive_canonical_role(track_str, skills=skills)
        if canonical_role:
            target_role = canonical_role
            topic_bank = self.get_topics_for_role(role=canonical_role, difficulty=normalized_difficulty, fallback_to_default=False)
            tech_topics = list(topic_bank.tech)
            tech_allied = self.extract_tech_allied_from_resume(
                resume_text=context_text,
                skills=skills,
                fallback_topics=topic_bank.tech_allied,
            )
        else:
            target_role = track_str
            topic_bank = self.get_topics_for_role(role=None, difficulty=normalized_difficulty, fallback_to_default=False)
            # Use candidate's skills as the primary tech topics
            tech_topics = [s for s in skills if s]
            if not tech_topics:
                tech_topics = [track_str]
            tech_allied = self.extract_tech_allied_from_resume(
                resume_text=context_text,
                skills=skills,
                fallback_topics=[],
            )

        topics = {
            "tech": tech_topics,
            "tech_allied": tech_allied,
            "behavioral": list(topic_bank.behavioral),
            "archetypes": topic_bank.archetypes,
            "depth_guidelines": topic_bank.depth_guidelines,
        }

        if is_data:
            ratio = {"data_engineering_querying": 2, "analysis_insights": 2, "behavioral": 1}
        elif is_design:
            ratio = {"core_design": 2, "design_strategy": 2, "behavioral": 1}
        else:
            question_ratio = self.compute_question_ratio(
                years_experience=None,
                has_resume_text=bool(context_text),
                has_skills=bool(skills),
            )
            ratio = {
                "tech": question_ratio.tech,
                "tech_allied": question_ratio.tech_allied,
                "behavioral": question_ratio.behavioral,
            }

        influence = {
            "target_role": target_role,
            "category": category,
            "difficulty": normalized_difficulty,
            "skills": skills,
            "experience_level": experience_level,
            "is_non_tech": False,
        }

        return {
            "target_role": target_role,
            "topics": topics,
            "ratio": ratio,
            "influence": influence,
            "is_non_tech": False,
        }

    def is_valid_role(self, role: str) -> bool:
        """Check if role is valid."""
        return self._role_manager.is_valid_role(role)
    
    def is_valid_difficulty(self, difficulty: str) -> bool:
        """Check if difficulty is valid."""
        return self._difficulty_manager.is_valid_difficulty(difficulty)
    
    def clear_cache(self) -> None:
        """Clear the topic cache to free memory."""
        cache_size = len(self._topic_cache)
        self._topic_cache.clear()
        logger.info(f"Cleared topic cache with {cache_size} entries")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics for monitoring."""
        return {
            "topic_cache_size": len(self._topic_cache),
            "role_cache_size": len(self._role_cache),
            "difficulty_cache_size": len(self._difficulty_cache),
        }


# Global service instance for backward compatibility
syllabus_service = SyllabusService()
