from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
            # The ONLY place a request-scoped realtime event is sent, and it is
            # after the only place a request-scoped transaction commits
            # (Architecture §8.4: a broadcast is a notification of already-true
            # state). Services stage events with `realtime.events.queue_event`;
            # if the commit above raises we fall into `except` and the staged
            # events are discarded with the transaction, so a rolled-back
            # check-in can never have been announced.
            #
            # Imported here rather than at module scope: app.realtime imports
            # nothing from app.core, but keeping the dependency one-directional
            # at import time avoids a cycle the first time it does.
            from app.realtime.events import dispatch_after_commit

            await dispatch_after_commit(session)
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
