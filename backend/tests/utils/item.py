from sqlmodel import Session

from app.core.security import get_password_hash
from app.models import Item, ItemCreate, User
from tests.utils.utils import random_email, random_lower_string


def create_random_item(db: Session) -> Item:
    user = User(
        email=random_email(),
        hashed_password=get_password_hash(random_lower_string()),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    owner_id = user.id
    assert owner_id is not None
    title = random_lower_string()
    description = random_lower_string()
    item_in = ItemCreate(title=title, description=description)

    item = Item.model_validate(item_in, update={"owner_id": owner_id})
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
