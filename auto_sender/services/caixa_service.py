from datetime import datetime, timezone
from dicts.caixa_dict import CaixaType
from services.broker_service import BrokerService

class CaixaService:
    contador_doacoes: int = 1

    @classmethod
    def publish_caixa(cls, sensor_id = "caixa_doacao_01"):
        caixa_pl = CaixaType(
            sensor_id           = sensor_id,
            timestamp           = datetime.now(timezone.utc).isoformat(),
            contador_doacoes    = cls.contador_doacoes,
            status_fenda        = "normal",
            volume_estimado     = float(cls.contador_doacoes * 3.0)
        )
        BrokerService.publish(caixa_pl)
        cls.contador_doacoes += 1
        return caixa_pl