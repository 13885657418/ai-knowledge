from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import get_password_hash
from app.models import User, UserCreate, UserUpdate
from tests.utils.utils import random_email, random_lower_string


def user_authentication_headers(
    *, client: TestClient, email: str, password: str
) -> dict[str, str]:
    data = {"username": email, "password": password}

    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=data)
    response = r.json()
    auth_token = response["access_token"]
    headers = {"Authorization": f"Bearer {auth_token}"}
    return headers


def create_random_user(db: Session) -> User:
    email = random_email()
    password = random_lower_string()
    user = User(
        email=email,
        hashed_password=get_password_hash(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authentication_token_from_email(
    *, client: TestClient, email: str, db: Session
) -> dict[str, str]:
    """
    Return a valid token for the user with given email.

    If the user doesn't exist it is created first.
    """
    password = random_lower_string()
    user = db.exec(select(User).where(User.email == email)).first()
    if not user:
        user = User(email=email, hashed_password=get_password_hash(password))
        db.add(user)
    else:
        user.hashed_password = get_password_hash(password)
        user.sqlmodel_update(UserUpdate(password=password).model_dump(exclude={"password"}))
        db.add(user)
    db.commit()
    db.refresh(user)

    return user_authentication_headers(client=client, email=email, password=password)
