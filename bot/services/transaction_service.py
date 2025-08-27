from ..db import crud
from ..utils.parsers import parse_amount


async def create_transaction(user_id: int, ttype: str, raw_amount: str, category: str = None, description: str = None, when=None):
# validações simples
   if ttype not in ("income", "expense"):
      raise ValueError("Tipo inválido. Use 'income' ou 'expense'.")
   amount = parse_amount(raw_amount)
   if amount <= 0:
      raise ValueError("O valor deve ser maior que zero.")
   tx = await crud.add_transaction(user_id, ttype, amount, category, description, when)
   return tx