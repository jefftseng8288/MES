"""Declarative base for MES SQLAlchemy models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all MES ORM models.

    No models are defined yet — Phase 1 introduces only the database
    scaffolding required to connect to PostgreSQL and run migrations.
    """
