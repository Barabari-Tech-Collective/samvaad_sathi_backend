import sqlalchemy
from typing import Any, List, Dict

from src.models.db.interview_question import InterviewQuestion
from src.repository.crud.base import BaseCRUDRepository


def _order_questions_with_followups(questions: List[InterviewQuestion]) -> List[InterviewQuestion]:
    """Reorder questions so follow-ups appear immediately after their parent question.
    
    For example, if Q4 has a follow-up that was created as Q6:
    - Original order: Q1, Q2, Q3, Q4, Q5, Q6(follow-up to Q4)
    - Reordered: Q1, Q2, Q3, Q4, Q6(follow-up to Q4), Q5
    
    This ensures questions are shown in logical order with follow-ups
    grouped with their parent questions.
    """
    # Separate into base questions and follow-ups
    base_questions: List[InterviewQuestion] = []
    follow_ups_by_parent: Dict[int, List[InterviewQuestion]] = {}
    
    for q in questions:
        if q.is_follow_up and q.parent_question_id is not None:
            if q.parent_question_id not in follow_ups_by_parent:
                follow_ups_by_parent[q.parent_question_id] = []
            follow_ups_by_parent[q.parent_question_id].append(q)
        else:
            base_questions.append(q)
    
    # Sort follow-ups for each parent by their order (in case of multiple follow-ups)
    for parent_id in follow_ups_by_parent:
        follow_ups_by_parent[parent_id].sort(key=lambda x: x.order)
    
    # Build final list: insert follow-ups after their parent
    result: List[InterviewQuestion] = []
    for q in base_questions:
        result.append(q)
        # Add any follow-ups for this question immediately after
        if q.id in follow_ups_by_parent:
            result.extend(follow_ups_by_parent[q.id])
    
    return result


class InterviewQuestionCRUDRepository(BaseCRUDRepository):
    async def create_batch(self, *, interview_id: int, questions_data: list[dict[str, Any]], resume_used: bool = False) -> list[InterviewQuestion]:
        """Create multiple interview questions with order, topic, text, and resume_used flag"""
        created: list[InterviewQuestion] = []
        for i, q_data in enumerate(questions_data):
            question = InterviewQuestion(
                interview_id=interview_id,
                text=str(q_data.get("text", "")),
                topic=q_data.get("topic"),
                category=q_data.get("category"),
                order=i + 1,  # 1-indexed ordering
                status="pending",
                resume_used=resume_used,
                is_follow_up=bool(q_data.get("is_follow_up", False)),
                parent_question_id=q_data.get("parent_question_id"),
                follow_up_strategy=q_data.get("follow_up_strategy"),
                audio_url=q_data.get("audio_url"),
            )
            self.async_session.add(question)
            created.append(question)
        
        await self.async_session.commit()
        for question in created:
            await self.async_session.refresh(question)
        return created

    async def list_by_interview(self, *, interview_id: int) -> list[InterviewQuestion]:
        """Get all questions for an interview, ordered with follow-ups after their parents.
        
        Questions are first ordered by their 'order' field, then reordered so that
        follow-up questions appear immediately after their parent question.
        """
        stmt = (
            sqlalchemy.select(InterviewQuestion)
            .where(InterviewQuestion.interview_id == interview_id)
            .order_by(InterviewQuestion.order.asc())
        )
        query = await self.async_session.execute(statement=stmt)
        rows = list(query.scalars().all())
        # Reorder so follow-ups appear after their parent questions
        return _order_questions_with_followups(rows)

    async def list_by_interview_cursor(
        self, *, interview_id: int, limit: int, cursor_id: int | None
    ) -> tuple[list[InterviewQuestion], int | None]:
        """Get questions for an interview with cursor pagination.
        
        Questions are ordered with follow-ups appearing after their parent question.
        Note: For simplicity, we fetch all questions and apply follow-up ordering,
        then paginate the result. This works well for typical interview sizes (5-10 questions).
        """
        # Fetch all questions first to apply proper follow-up ordering
        stmt = (
            sqlalchemy.select(InterviewQuestion)
            .where(InterviewQuestion.interview_id == interview_id)
            .order_by(InterviewQuestion.order.asc())
        )
        query = await self.async_session.execute(statement=stmt)
        all_rows = list(query.scalars().all())
        
        # Reorder so follow-ups appear after their parent questions
        ordered_rows = _order_questions_with_followups(all_rows)
        
        # Apply cursor pagination on the ordered list
        if cursor_id is not None:
            # Find the index after the cursor
            cursor_index = -1
            for i, q in enumerate(ordered_rows):
                if q.id == cursor_id:
                    cursor_index = i
                    break
            if cursor_index >= 0:
                ordered_rows = ordered_rows[cursor_index + 1:]
        
        # Apply limit
        next_cursor: int | None = None
        if len(ordered_rows) > limit:
            next_cursor = ordered_rows[limit - 1].id
            ordered_rows = ordered_rows[:limit]
        
        return ordered_rows, next_cursor

    async def get_by_id(self, *, question_id: int) -> InterviewQuestion | None:
        """Get a question by ID"""
        stmt = sqlalchemy.select(InterviewQuestion).where(InterviewQuestion.id == question_id)
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()

    async def get_by_id_and_user(self, *, question_id: int, user_id: int) -> InterviewQuestion | None:
        """Get a question by ID, ensuring it belongs to the specified user"""
        stmt = (
            sqlalchemy.select(InterviewQuestion)
            .join(InterviewQuestion.interview)
            .where(
                InterviewQuestion.id == question_id,
                InterviewQuestion.interview.has(user_id=user_id)
            )
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()

    async def update_status(self, *, question_id: int, status: str) -> InterviewQuestion | None:
        """Update the status of a question"""
        stmt = sqlalchemy.select(InterviewQuestion).where(InterviewQuestion.id == question_id)
        query = await self.async_session.execute(statement=stmt)
        question = query.scalar_one_or_none()
        
        if question:
            question.status = status
            await self.async_session.commit()
            await self.async_session.refresh(question)
        
        return question

    async def get_questions_without_attempts(self, *, interview_id: int) -> list[InterviewQuestion]:
        """Get all questions for an interview that don't have any attempts"""
        from src.models.db.question_attempt import QuestionAttempt
        
        # Subquery to get question IDs that have attempts
        attempted_question_ids = sqlalchemy.select(QuestionAttempt.question_id).where(
            QuestionAttempt.interview_id == interview_id,
            QuestionAttempt.question_id.is_not(None)
        ).distinct()
        
        # Main query to get questions without attempts
        stmt = (
            sqlalchemy.select(InterviewQuestion)
            .where(
                InterviewQuestion.interview_id == interview_id,
                InterviewQuestion.id.not_in(attempted_question_ids)
            )
            .order_by(InterviewQuestion.order.asc())
        )
        
        query = await self.async_session.execute(statement=stmt)
        rows = query.scalars().all()
        return list(rows)

    async def get_questions_with_attempts_count(self, *, interview_id: int) -> int:
        """Get count of questions that have attempts"""
        from src.models.db.question_attempt import QuestionAttempt
        
        stmt = (
            sqlalchemy.select(sqlalchemy.func.count(sqlalchemy.distinct(QuestionAttempt.question_id)))
            .where(
                QuestionAttempt.interview_id == interview_id,
                QuestionAttempt.question_id.is_not(None)
            )
        )
        
        query = await self.async_session.execute(statement=stmt)
        return query.scalar() or 0

    async def set_parent_question(
        self,
        *,
        question_id: int,
        parent_question_id: int,
    ) -> InterviewQuestion | None:
        """Backfill parent_question_id for follow-up questions when missing."""
        stmt = sqlalchemy.select(InterviewQuestion).where(InterviewQuestion.id == question_id)
        query = await self.async_session.execute(statement=stmt)
        question = query.scalar_one_or_none()
        if not question:
            return None

        question.parent_question_id = parent_question_id
        await self.async_session.commit()
        await self.async_session.refresh(question)
        return question

    async def get_follow_up_for_parent(self, *, parent_question_id: int) -> InterviewQuestion | None:
        """Return the follow-up question for a given parent question if it exists."""
        stmt = (
            sqlalchemy.select(InterviewQuestion)
            .where(
                InterviewQuestion.parent_question_id == parent_question_id,
                InterviewQuestion.is_follow_up.is_(True),
            )
            .limit(1)
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()

    async def create_follow_up_question(
        self,
        *,
        interview_id: int,
        parent_question_id: int,
        text: str,
        topic: str | None = None,
        category: str | None = None,
        strategy: str | None = None,
    ) -> InterviewQuestion:
        """Persist a follow-up question linked to a parent question."""
        order_stmt = sqlalchemy.select(sqlalchemy.func.max(InterviewQuestion.order)).where(
            InterviewQuestion.interview_id == interview_id
        )
        current_order = await self.async_session.execute(order_stmt)
        next_order = int(current_order.scalar() or 0) + 1

        follow_up = InterviewQuestion(
            interview_id=interview_id,
            text=text,
            topic=topic,
            category=category,
            status="pending",
            order=next_order,
            resume_used=False,
            is_follow_up=True,
            parent_question_id=parent_question_id,
            follow_up_strategy=strategy,
        )
        self.async_session.add(follow_up)
        await self.async_session.commit()
        await self.async_session.refresh(follow_up)
        return follow_up
