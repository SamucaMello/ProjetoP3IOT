import datetime
import json
import paho.mqtt.client as mqtt

from time import sleep
from datetime import datetime, timezone

from dicts.caixa_dict import CaixaType
from services.caixa_service import CaixaService
from services.config_service import ConfigService



class App:
    @classmethod
    def start(cls):
        while True:
            try:
                pl = CaixaService.publish_caixa()
                print(f"Enviado -> {str(pl)}")
            except Exception as ex:
                print(f"Algo deu errado: -> {str(ex)}")

            sleep(ConfigService.load_config()['DELAY'])


App.start()