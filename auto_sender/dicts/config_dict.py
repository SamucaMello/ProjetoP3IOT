from typing import TypedDict, Union

class ConfigType(TypedDict):
    BROKER_HOST: str
    BROKER_PORT:Union[int, str]
    BROKER_TOPIC:str
    DELAY:int
    RUNNING:bool