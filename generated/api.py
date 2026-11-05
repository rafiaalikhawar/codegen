from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

try:
    from .models import Base, Book, Member

    from .schemas import (
        
        BookCreate,
        BookUpdate,
        BookRead,
        
        MemberCreate,
        MemberUpdate,
        MemberRead,
        
    )
except ImportError:
    from models import Base, Book, Member

    from schemas import (
        
        BookCreate,
        BookUpdate,
        BookRead,
        
        MemberCreate,
        MemberUpdate,
        MemberRead,
        
    )

engine = create_engine("sqlite:///./generated_app.db")
Base.metadata.create_all(engine)

app = FastAPI(title="LibraryManagement API")



@app.post("/book", response_model=BookRead)
def create_book(data: BookCreate):
    with Session(engine) as session:
        obj = Book(**data.model_dump(exclude_unset=True))
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj


@app.get("/book", response_model=list[BookRead])
def list_book():
    with Session(engine) as session:
        return session.query(Book).all()


@app.get("/book/{item_id}", response_model=BookRead)
def get_book(item_id: int):
    with Session(engine) as session:
        obj = session.get(Book, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return obj


@app.put("/book/{item_id}", response_model=BookRead)
def update_book(item_id: int, data: BookUpdate):
    with Session(engine) as session:
        obj = session.get(Book, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")

        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(obj, key, value)

        session.commit()
        session.refresh(obj)
        return obj


@app.delete("/book/{item_id}")
def delete_book(item_id: int):
    with Session(engine) as session:
        obj = session.get(Book, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")

        session.delete(obj)
        session.commit()
        return {"message": "Deleted successfully"}



@app.post("/member", response_model=MemberRead)
def create_member(data: MemberCreate):
    with Session(engine) as session:
        obj = Member(**data.model_dump(exclude_unset=True))
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj


@app.get("/member", response_model=list[MemberRead])
def list_member():
    with Session(engine) as session:
        return session.query(Member).all()


@app.get("/member/{item_id}", response_model=MemberRead)
def get_member(item_id: int):
    with Session(engine) as session:
        obj = session.get(Member, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return obj


@app.put("/member/{item_id}", response_model=MemberRead)
def update_member(item_id: int, data: MemberUpdate):
    with Session(engine) as session:
        obj = session.get(Member, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")

        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(obj, key, value)

        session.commit()
        session.refresh(obj)
        return obj


@app.delete("/member/{item_id}")
def delete_member(item_id: int):
    with Session(engine) as session:
        obj = session.get(Member, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")

        session.delete(obj)
        session.commit()
        return {"message": "Deleted successfully"}



