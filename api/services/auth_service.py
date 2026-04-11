from fastapi import Depends, HTTPException, Cookie
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from config import settings
from models import User, get_db


async def get_current_user(
    token: str = Cookie(None),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(401, "Not authenticated")

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Invalid token")
    except JWTError:
        raise HTTPException(401, "Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(401, "User not found")

    return user
