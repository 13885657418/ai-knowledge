from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.core.config import settings
from app.models import User, UserCreate

engine = create_async_engine(str(settings.ASYNC_SQLALCHEMY_DATABASE_URI))
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# make sure all SQLModel models are imported (app.models) before initializing DB
# otherwise, SQLModel might fail to initialize relationships properly
# for more details: https://github.com/fastapi/full-stack-fastapi-template/issues/28


async def init_db(session: AsyncSession) -> None:
    # Tables should be created with Alembic migrations
    # But if you don't want to use migrations, create
    # the tables un-commenting the next lines
    # from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    # SQLModel.metadata.create_all(engine)

    result = await session.execute(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    )
    user = result.scalar_one_or_none()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            is_superuser=True,
        )
        await crud.create_user(session=session, user_create=user_in)

    # 初始化默认 Prompt 版本（设计文档 4.6：保证存在一个 active 模板）
    try:
        from app.services.prompt_service import PromptService

        await PromptService(session).seed_default()
    except Exception as exc:  # 不阻断初始化，仅告警
        import logging

        logging.getLogger(__name__).warning("seed_default prompt failed: %s", exc)
