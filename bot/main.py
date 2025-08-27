import asyncio
import logging
from telegram.ext import ApplicationBuilder
from telegram import BotCommand
from config import Env
from handlers.base import register_handlers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    app = ApplicationBuilder().token(Env.TELEGRAM_TOKEN).build()
    register_handlers(app)

    await app.initialize()
    await app.start()

    commands = [
        BotCommand("comprarapida", "Compra rápida"),
        BotCommand("add", "Adicionar uma nova transação"),
        BotCommand("carteira", "Visualize a carteira do dia"),
        BotCommand("meusdados", "Meu Dados"),
        BotCommand("resumo", "Resumo do Mês"),
        BotCommand("listacategorias", "Listar categorias ou transações"),
        BotCommand("start", "Mostrar mensagem de boas-vindas"),
        BotCommand("exit", "Reiniciar"),
    ]

    try:
        await app.bot.set_my_commands(commands)
        logger.info("Comandos registrados com sucesso no bot.")
    except AttributeError:
        logger.warning("app.bot não tem set_my_commands — sua versão da lib pode ser diferente.")
    except Exception as e:
        logger.exception("Falha ao setar comandos: %s", e)

    try:
        await app.updater.start_polling()
    except AttributeError:
        logger.info("app.updater não existe; usando run_polling().")
        app.run_polling()
        return

    logger.info("Bot rodando. Ctrl+C para parar.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Parando o bot ...")
    finally:
        try:
            await app.updater.stop_polling()
        except Exception:
            pass
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
