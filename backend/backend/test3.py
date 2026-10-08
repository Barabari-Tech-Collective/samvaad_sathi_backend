import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from src.services.analytics import AnalyticsService
from src.config.manager import settings

async def main():
    db_url = getattr(settings, "SQLALCHEMY_DATABASE_URI", getattr(settings, "DATABASE_URL", None))
    if not db_url:
        db_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/samvaad_saathi" # fallback
    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        service = AnalyticsService(db)
        res = await service.get_roles_performance_summary()
        print(f"Results: {res}")

if __name__ == "__main__":
    asyncio.run(main())
