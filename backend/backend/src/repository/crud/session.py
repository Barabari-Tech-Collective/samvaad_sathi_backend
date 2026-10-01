import datetime
import secrets

import sqlalchemy

from src.models.db.session import Session
from src.repository.crud.base import BaseCRUDRepository


class SessionCRUDRepository(BaseCRUDRepository):
    async def create_session(self, *, user_id: int, expiry_minutes: int = 60) -> Session:
        token = secrets.token_urlsafe(48)
        expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=expiry_minutes)

        new_session = Session(user_id=user_id, token=token, expiry=expiry)
        self.async_session.add(new_session)
        await self.async_session.commit()
        await self.async_session.refresh(new_session)
        return new_session

    async def get_session_by_token(self, *, token: str) -> Session | None:
        stmt = sqlalchemy.select(Session).where(Session.token == token)
        query = await self.async_session.execute(statement=stmt)
        return query.scalar()  # type: ignore

    async def expire_session_soon(self, *, session: Session, grace_seconds: int) -> Session:
        """Bring a session's expiry forward to at most `grace_seconds` from now.

        Used when rotating a refresh token: the replaced token stays briefly valid so
        that requests already in flight with it still succeed, instead of the first
        rotation winning and the rest 401-ing their way into a logout.

        Only ever shortens. A session already closer to expiry than the grace window
        keeps its original expiry, so this cannot hand a nearly-dead token extra life.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=grace_seconds)
        if session.expiry > cutoff:
            session.expiry = cutoff
            self.async_session.add(session)
            await self.async_session.commit()
        return session

    async def delete_expired_sessions(self, *, user_id: int) -> int:
        """Delete this user's already-expired session rows. Returns how many went.

        Rotation supersedes a token by expiring it rather than deleting it, so without
        this the table gains a dead row per refresh and never loses one - and
        get_session_by_token pays for that growth on every single refresh. Scoped to one
        user and run during their own refresh so the cost stays proportional and no
        scheduler is needed.
        """
        stmt = sqlalchemy.delete(Session).where(
            Session.user_id == user_id,
            Session.expiry < datetime.datetime.now(datetime.timezone.utc),
        )
        result = await self.async_session.execute(statement=stmt)
        await self.async_session.commit()
        return result.rowcount or 0

    async def delete_session_by_token(self, *, token: str) -> bool:
        """Delete a session row by its token. Returns True if a row was deleted."""
        # Fetch the session to ensure existence and leverage ORM delete for compatibility
        stmt = sqlalchemy.select(Session).where(Session.token == token)
        query = await self.async_session.execute(statement=stmt)
        entity: Session | None = query.scalar()  # type: ignore
        if not entity:
            return False
        await self.async_session.delete(entity)
        await self.async_session.commit()
        return True


