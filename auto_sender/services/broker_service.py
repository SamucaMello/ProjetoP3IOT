from time import sleep

from paho.mqtt.client import Client
from services.config_service import ConfigService
from services.payload_service import PayloadService

class BrokerService:
    client:Client = Client(callback_api_version=2)


    @classmethod
    def publish(cls, payload:dict):
        config =  ConfigService.load_config()
        pl     =  PayloadService.create_payload(payload)

        cls.client.connect(config["BROKER_HOST"], config["BROKER_PORT"])
        cls.client.publish(config["BROKER_TOPIC"], pl).wait_for_publish()

