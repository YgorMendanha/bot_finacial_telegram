from telegram import Update
from db.session import get_session
from db.models import Profile
from sqlalchemy.future import select

async def auth(update: Update):
    
    user_id = update.message.from_user.id

    async with get_session() as session:
        result = await session.execute(
            select(Profile).where(Profile.telegram_id == user_id)
        )
        profile = result.scalar_one_or_none()

    if not profile:
        await update.message.reply_text(
            "❌ Você ainda não possui uma conta.\n"
            "Use /start para criar seu perfil e começar a usar o  "
        )
        return None

    return profile
