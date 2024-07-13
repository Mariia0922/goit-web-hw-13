import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import JWTStrategy, AuthenticationBackend
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.manager import BaseUserManager
from fastapi_users.password import PasswordHelper
from fastapi_users import FastAPIUsers, UserManager
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import Column, String, Integer, create_engine, ForeignKey, Date
from sqlalchemy.ext.declarative import DeclarativeMeta, declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session
from sqlalchemy.exc import IntegrityError
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")
SECRET = os.getenv("SECRET")


cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

Base: DeclarativeMeta = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class UserTable(Base, SQLAlchemyUserDatabase):
    __tablename__ = "user"
    contacts = relationship("Contact", back_populates="owner")

class UserCreate(BaseModel):
    email: EmailStr
    password: str

class UserRead(BaseModel):
    id: int
    email: EmailStr
    avatar: Optional[str] = None  

    class Config:
        orm_mode = True

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    avatar: Optional[str] = None  

class UserDB(UserTable):
    pass

class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, index=True)
    last_name = Column(String, index=True)
    email = Column(String, index=True)
    phone_number = Column(String, index=True)
    birthday = Column(Date)
    additional_info = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("user.id"))

    owner = relationship("UserTable", back_populates="contacts")

class ContactCreate(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    phone_number: str
    birthday: datetime
    additional_info: Optional[str] = None

class ContactUpdate(ContactCreate):
    pass

def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=None,
    get_strategy=get_jwt_strategy,
)

class UserManager(BaseUserManager[UserDB, int]):
    user_db_model = UserDB
    password_helper = PasswordHelper()

    async def on_after_register(self, user: UserDB, request=None):
        print(f"User {user.id} has registered.")

    async def create(self, user: UserCreate, safe: bool = False) -> UserDB:
        await self.validate_password(user.password)
        hashed_password = self.password_helper.hash(user.password)
        db_user = UserDB(
            email=user.email,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=False,
        )
        try:
            await self.user_db_model.create(db_user)
            return db_user
        except IntegrityError:
            raise HTTPException(status_code=409, detail="User with this email already exists")

async def get_user_manager(user_db=Depends(SQLAlchemyUserDatabase(UserDB, SessionLocal()))):
    yield UserManager(user_db)

fastapi_users = FastAPIUsers[UserCreate, UserDB, int](
    get_user_manager,
    [auth_backend],
)

app = FastAPI()


origins = [
    "http://localhost:3000",  
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)

app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)

app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)

@app.post("/users/{user_id}/avatar/")
async def upload_avatar(user_id: int, file: bytes, user: UserDB = Depends(fastapi_users.current_user)):
    
    result = cloudinary.uploader.upload(file)
    avatar_url = result['url']
    
    
    user.avatar = avatar_url
    
    return {"avatar_url": avatar_url}

@app.post("/contacts/", response_model=ContactCreate)
async def create_contact(contact: ContactCreate, db: Session = Depends(SessionLocal), user: UserDB = Depends(fastapi_users.current_user)):
    db_contact = Contact(**contact.dict(), owner_id=user.id)
    db.add(db_contact)
    db.commit()
    db.refresh(db_contact)
    return db_contact

@app.get("/contacts/", response_model=list[ContactCreate])
async def read_contacts(skip: int = 0, limit: int = 10, db: Session = Depends(SessionLocal), user: UserDB = Depends(fastapi_users.current_user)):
    contacts = db.query(Contact).filter(Contact.owner_id == user.id).offset(skip).limit(limit).all()
    return contacts

@app.get("/contacts/{contact_id}", response_model=ContactCreate)
async def read_contact(contact_id: int, db: Session = Depends(SessionLocal), user: UserDB = Depends(fastapi_users.current_user)):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.owner_id == user.id).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact

@app.put("/contacts/{contact_id}", response_model=ContactCreate)
async def update_contact(contact_id: int, contact: ContactUpdate, db: Session = Depends(SessionLocal), user: UserDB = Depends(fastapi_users.current_user)):
    db_contact = db.query(Contact).filter(Contact.id == contact_id, Contact.owner_id == user.id).first()
    if db_contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    for key, value in contact.dict().items():
        setattr(db_contact, key, value)
    db.commit()
    db.refresh(db_contact)
    return db_contact

@app.delete("/contacts/{contact_id}", response_model=ContactCreate)
async def delete_contact(contact_id: int, db: Session = Depends(SessionLocal), user: UserDB = Depends(fastapi_users.current_user)):
    db_contact = db.query(Contact).filter(Contact.id == contact_id, Contact.owner_id == user.id).first()
    if db_contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    db.delete(db_contact)
    db.commit()
    return db_contact

Base.metadata.create_all(bind=engine)
