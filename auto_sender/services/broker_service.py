from paho.mqtt.client import Client
from services.config_service import ConfigService
from services.payload_service import PayloadService

class BrokerService:
    @staticmethod
    def publish(payload:dict):
        config =  ConfigService.load_config()
        pl     =  PayloadService.create_payload(payload)

        cl = Client(callback_api_version=2)
        cl.connect(config["BROKER_HOST"], config["BROKER_PORT"])

        cl.publish(config["BROKER_TOPIC"], pl).wait_for_publish()
        cl.disconnect()

        