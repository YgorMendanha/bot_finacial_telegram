import re
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from  config import Env

DATABASE_URL = Env.DATABASE_URL  
DATABASE_URL = re.sub(r'^(postgres)(ql)?\:', r'postgresql+asyncpg:', DATABASE_URL)

engine = create_async_engine(DATABASE_URL, echo=True)

AsyncSessionMaker = async_sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

@asynccontextmanager
async def get_session():
    async with AsyncSessionMaker() as session:
        yield session

async def init_db():
    import  db.models
    async with engine.begin() as conn:      
        await conn.run_sync(Base.metadata.create_all)
    print("📦 init_db finalizado")
