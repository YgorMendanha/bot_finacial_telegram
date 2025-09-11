import enum
import sqlalchemy as sa
from sqlalchemy import Column, Integer, String, Float, Date, Text, Enum, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from  db.session import Base


# ===== ENUMS =====
class TransactionType(enum.Enum):
    ENTRADA = "entrada"
    SAIDA = "saida"


class CategoryType(enum.Enum):
    VARIAVEL = "variavel"
    FIXA = "fixa"


class CurrencyEnum(enum.Enum):
    BRL = "BRL"
    USD = "USD"
    EUR = "EUR"


class DebtStatus(enum.Enum):
    OPEN = "open"
    PAID = "paid"

class DebtType(enum.Enum):
    REAL = "real"      
    PARCELADO = "parcelado"


# ===== MODELS =====
class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=True)
    emergency_fund = Column(Float, nullable=False, default=0.0)
    telegram_id = Column(Integer, unique=True, nullable=False)

    # Relações
    accounts = relationship("Account", back_populates="profile", cascade="all, delete-orphan")
    debts = relationship("Debt", back_populates="profile", cascade="all, delete-orphan")
    categories = relationship("Category", back_populates="profile", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="profile", cascade="all, delete-orphan")


    def __repr__(self):
        return f"<Profile(id={self.id}, name={self.name!r})>"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(80), nullable=False)
    balance = Column(Float, nullable=False, default=0.0)
    currency = Column(Enum(CurrencyEnum, name="currencyenum", create_type=False),
                      nullable=False, server_default=sa.text("'BRL'"))

    profile = relationship("Profile", back_populates="accounts")
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan", foreign_keys="[Transaction.account_id]")
    type = Column(String, nullable=False) 

    def __repr__(self):
        return f"<Account(id={self.id}, name={self.name!r}, balance={self.balance})>"


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    type = Column(Enum(CategoryType), nullable=False, default=CategoryType.VARIAVEL)

    profile = relationship("Profile", back_populates="categories")
    transactions = relationship("Transaction", back_populates="category", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Category(id={self.id}, name='{self.name}', profile_id={self.profile_id})>"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    settlement_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    transfer_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)

    type = Column(Enum(TransactionType), nullable=False)
    value = Column(Float, nullable=False)
    date = Column(Date, nullable=False)
    description = Column(String(255), nullable=True)    
    is_transfer = Column(Boolean, nullable=False, default=False)
    balance_before = Column(Float, nullable=True) 

    is_settled = Column(Boolean, default=False, nullable=False)

    # Relações
    account = relationship("Account", back_populates="transactions", foreign_keys=[account_id])
    category = relationship("Category", back_populates="transactions")
    profile = relationship("Profile", back_populates="transactions")

    def __repr__(self):
        return (
            f"<Transaction(id={self.id}, type={self.type}, value={self.value}, "
            f"date={self.date}, account_id={self.account_id}, category_id={self.category_id}, "
            f"balance_before={self.balance_before})>"
        )

class Debt(Base):
    __tablename__ = "debts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    creditor = Column(String(120), nullable=False)
    monthly_payment = Column(Float, nullable=False)
    months = Column(Integer, nullable=False, default=1)
    description = Column(Text, nullable=True)
    status = Column(Enum(DebtStatus, name="debtstatus"), nullable=False, default=DebtStatus.OPEN)
    type = Column(Enum(DebtType, name="debttype"), nullable=False, default=DebtType.REAL)
    profile = relationship("Profile", back_populates="debts")

    @property
    def total_amount(self):
        return self.monthly_payment * self.months

    def mark_as_paid(self):
        self.status = DebtStatus.PAID

    def __repr__(self):
        return f"<Debt(id={self.id}, creditor={self.creditor!r}, monthly_payment={self.monthly_payment}, months={self.months}, status={self.status})>"
