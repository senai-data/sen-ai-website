from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import Client, ClientCredit, UserClient, get_db
from services.auth_service import get_current_user
from services.sanitize import strip_tags

router = APIRouter()


class ClientResponse(BaseModel):
    id: str
    name: str
    brand: str | None
    apps: dict | None = None

    model_config = {"from_attributes": True}


class ClientCreate(BaseModel):
    name: str
    brand: str | None = None


@router.get("/", response_model=list[ClientResponse])
async def list_clients(user=Depends(get_current_user), db: Session = Depends(get_db)):
    links = db.query(UserClient).filter(UserClient.user_id == user.id).all()
    client_ids = [link.client_id for link in links]
    clients = db.query(Client).filter(Client.id.in_(client_ids)).all()
    return [ClientResponse(id=str(c.id), name=c.name, brand=c.brand, apps=c.apps) for c in clients]


@router.post("/")
async def create_client(req: ClientCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Check if user already has a client
    existing = db.query(UserClient).filter(UserClient.user_id == user.id).first()
    if existing:
        client = db.query(Client).filter(Client.id == existing.client_id).first()
        return ClientResponse(id=str(client.id), name=client.name, brand=client.brand, apps=client.apps)

    # Create new client + link user as owner
    # Welcome bonus is now granted on email verification (H3), not here
    client = Client(name=strip_tags(req.name), brand=strip_tags(req.brand))
    db.add(client)
    db.flush()

    db.add(UserClient(user_id=user.id, client_id=client.id, role="owner"))

    db.commit()
    db.refresh(client)

    return ClientResponse(id=str(client.id), name=client.name, brand=client.brand, apps=client.apps)
