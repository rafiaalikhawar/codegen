from datetime import date
from pydantic import BaseModel, ConfigDict


class BookBase(BaseModel):

    title: str

    author: str

    available: bool = True



class BookCreate(BookBase):
    pass


class BookUpdate(BaseModel):

    title: str | None = None

    author: str | None = None

    available: bool | None = None



class BookRead(BookBase):
    model_config = ConfigDict(from_attributes=True)

    id: int




class MemberBase(BaseModel):

    name: str

    email: str



class MemberCreate(MemberBase):
    pass


class MemberUpdate(BaseModel):

    name: str | None = None

    email: str | None = None



class MemberRead(MemberBase):
    model_config = ConfigDict(from_attributes=True)

    id: int




