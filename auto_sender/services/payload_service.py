import json
from typing import TypedDict

class PayloadService:
    @staticmethod
    def create_payload(payload:dict) -> str:
        return json.dumps(payload)