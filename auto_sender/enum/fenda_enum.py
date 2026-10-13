from enum import Enum

class StatusFenda(str, Enum):
    NORMAL          = "normal"
    FENDA_OBSTRUIDA = "fenda_obstruida"
    CAIXA_CHEIA     = "caixa_cheia"