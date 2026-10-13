
from typing import TypedDict

class CaixaType(TypedDict):
    sensor_id: str
    timestamp: str
    contador_doacoes: int
    status_fenda: str
    volume_estimado: float
