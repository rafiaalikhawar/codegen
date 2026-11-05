from sqlalchemy import Column, Integer, String, Boolean, Float, Date
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Book(Base):
    __tablename__ = "book"


    id = Column(
        Integer,
        primary_key=True,
        nullable=False,
        unique=False
    )


    title = Column(
        String(255),
        primary_key=False,
        nullable=False,
        unique=False
    )


    author = Column(
        String(255),
        primary_key=False,
        nullable=False,
        unique=False
    )


    available = Column(
        Boolean,
        primary_key=False,
        nullable=True,
        unique=False,
        default=True
    )




class Member(Base):
    __tablename__ = "member"


    id = Column(
        Integer,
        primary_key=True,
        nullable=False,
        unique=False
    )


    name = Column(
        String(255),
        primary_key=False,
        nullable=False,
        unique=False
    )


    email = Column(
        String(255),
        primary_key=False,
        nullable=False,
        unique=True
    )




