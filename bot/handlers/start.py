from telegram import Update
from telegram.ext import ContextTypes
from db.session import get_session
from db.models import Profile, Account, CurrencyEnum
from sqlalchemy.future import select


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.full_name

    await update.message.reply_text("⌛ Verificando...")
    profile, created = await get_or_create_user(user_id, user_name)

    msg = (
        "Sua conta padrão já foi criada com sucesso."
        if created else
        "Você já tem sua conta configurada. ✅"
    )

    await update.message.reply_text(
        f"Olá {profile.name}! 👋\n\n"
        f"{msg}\n\n"
        "🔒 Autenticação:\n"
        "Você está autenticado automaticamente através do seu perfil do Telegram. "
        "Isso significa que apenas você consegue acessar os seus dados, e não é necessário criar senha.\n\n"
        "💼 Privacidade:\n"
        "Todas as suas informações financeiras (contas, transações e dívidas) "
        "estão vinculadas exclusivamente à sua conta do Telegram e são mantidas de forma segura.\n\n"
        "Agora você pode começar a registrar suas receitas e despesas! ✅"
    )


async def get_or_create_user(telegram_id: int, name: str):
    async with get_session() as session:
        result = await session.execute(
            select(Profile).where(Profile.telegram_id == telegram_id)
        )
        profile = result.scalar_one_or_none()

        if not profile:
            profile = Profile(telegram_id=telegram_id, name=name)
            session.add(profile)
            await session.flush()
            await session.refresh(profile)

            account_default = Account(
                profile_id=profile.id,
                name="Disponível",
                balance=0,
                currency=CurrencyEnum.BRL,
                type='bank'
            )

            account_principal = Account(
                profile_id=profile.id,
                name="Principal",
                balance=0,
                currency=CurrencyEnum.BRL,
                type='bank'
            )

            session.add_all([account_default, account_principal])
            await session.commit()

            print(f"✅ Novo usuário criado: {name} ({telegram_id})")
            return profile, True
        else:
            print(f"🔑 Usuário já existe: {profile.name} ({telegram_id})")
            return profile, False
