from decimal import Decimal, InvalidOperation


def parse_amount(raw: str) -> float:
   """Aceita '12.34' ou '12,34' e retorna float; levanta ValueError se inválido."""
   if raw is None:
      raise ValueError("Valor vazio")
      normalized = raw.replace(".", ".").replace(",", ".")
      try:
         d = Decimal(normalized)
      except InvalidOperation:
         raise ValueError("Valor inválido")
      return float(d)