import asyncio
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, delete

from app.core.config import settings
from app.core.db import async_session, init_db
from app.main import app
from app.models import Item, User
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers

sync_engine = create_engine(str(settings.SQLALCHEMY_DATABASE_URI))


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session, None, None]:
    async def _setup() -> None:
        async with async_session() as session:
            await init_db(session)

    asyncio.run(_setup())

    with Session(sync_engine) as session:
        yield session
        session.exec(delete(Item))
        session.exec(delete(User))
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
