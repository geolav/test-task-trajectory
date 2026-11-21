"""Модели CRM-синхронизации."""
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, ForeignKey, func, MetaData
from sqlalchemy.dialects.postgresql import JSONB

naming_convention = {
    "ix": 'ix_%(column_0_label)s',
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}
db = SQLAlchemy(metadata=MetaData(naming_convention=naming_convention))


class AbstractCustomer(db.Model):
    """Агрегат CRM-сущностей."""
    __tablename__ = "abstract_customer"
    id = Column(db.BigInteger(), nullable=False, primary_key=True)
    send_methods = Column(JSONB)
    created_at = Column(db.DateTime, server_default=func.now())
    updated_at = Column(db.DateTime, onupdate=func.now())


class AmoCustomer(db.Model):
    """Покупатель CRM."""
    __tablename__ = "amo_customer"
    crm_id = Column(db.BigInteger(), nullable=False, primary_key=True, autoincrement=False)
    abc_id = Column(db.BigInteger(),
                    ForeignKey("abstract_customer.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    login = Column(db.Text, index=True)
    name = Column(db.Text, index=True)
    data = Column(JSONB, nullable=False)
    created_at = Column(db.DateTime, server_default=func.now())
    updated_at = Column(db.DateTime)


class AmoLead(db.Model):
    __tablename__ = "amo_lead"
    crm_id = Column(db.BigInteger(), nullable=False, primary_key=True, autoincrement=False)
    abc_id = Column(db.BigInteger(),
                    ForeignKey("abstract_customer.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    login = Column(db.Text, index=True)
    name = Column(db.Text, index=True)
    data = Column(JSONB, nullable=False)
    created_at = Column(db.DateTime, server_default=func.now())
    updated_at = Column(db.DateTime)


class AmoContact(db.Model):
    __tablename__ = "amo_contact"
    abc_id = Column(db.BigInteger(),
                    ForeignKey("abstract_customer.id", ondelete="CASCADE"),
                    nullable=False, primary_key=True)
    crm_id = Column(db.BigInteger(), nullable=False, primary_key=True)
    name = Column(db.Text, index=True)
    data = Column(JSONB, nullable=False)
    created_at = Column(db.DateTime, server_default=func.now())
    updated_at = Column(db.DateTime)


class AmoCompany(db.Model):
    __tablename__ = "amo_company"
    abc_id = Column(db.BigInteger(),
                    ForeignKey("abstract_customer.id", ondelete="CASCADE"),
                    nullable=False, primary_key=True)
    crm_id = Column(db.BigInteger(), nullable=False, primary_key=True)
    name = Column(db.Text, index=True)
    data = Column(JSONB, nullable=False)
    created_at = Column(db.DateTime, server_default=func.now())
    updated_at = Column(db.DateTime)
