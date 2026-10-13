from datetime import datetime, timezone
from enum import Enum

from dicts.caixa_dict import CaixaType
from services.broker_service import BrokerService

class StatusFenda(str, Enum):
    NORMAL            = "normal"
    FENDA_OBSTRUIDA = "fenda_obstruida"
    CAIXA_CHEIA     = "caixa_cheia"


class CaixaService:
    contador_doacoes: int = 1
    tempo_obstrucao_inicio: datetime | None = None
    CAPACIDADE_MAXIMA: int = 30  

    @classmethod
    def publish_caixa(cls, sensor_id: str = "caixa_doacao_01", sensor_travado: bool = False):
        agora = datetime.now(timezone.utc)
        
        if cls.contador_doacoes >= cls.CAPACIDADE_MAXIMA:
            status = StatusFenda.CAIXA_CHEIA
        elif sensor_travado:
            if cls.tempo_obstrucao_inicio is None:
                cls.tempo_obstrucao_inicio = agora

            tempo_decorrido = (agora - cls.tempo_obstrucao_inicio).total_seconds()

            if tempo_decorrido > 10:
                status = StatusFenda.FENDA_OBSTRUIDA
            else:
                status = StatusFenda.NORMAL
        else:
            # 3. Funcionamento normal
            cls.tempo_obstrucao_inicio = None
            status = StatusFenda.NORMAL

        caixa_pl = CaixaType(
            sensor_id           = sensor_id,
            timestamp           = agora.isoformat(),
            contador_doacoes    = cls.contador_doacoes,
            status_fenda        = status.value,
            volume_estimado     = float(cls.contador_doacoes * 3.0)
        )

        BrokerService.publish(caixa_pl)
        cls.contador_doacoes += 1
        return caixa_pl
