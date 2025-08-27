from  db.session import init_db
import asyncio

# Função principal
async def main():
    await init_db()
    
if __name__ == "__main__":
    asyncio.run(main())