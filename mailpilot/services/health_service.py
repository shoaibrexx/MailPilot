from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db


def database_is_available():
    try:
        db.session.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
