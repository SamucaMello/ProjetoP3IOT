import json
import os

from dicts.config_dict import ConfigType


class ConfigService:
    @staticmethod
    def load_config(config_file_path:str = "./config.json") -> ConfigType:
        if not os.path.exists(config_file_path):
            raise FileNotFoundError("Arquivo de config não encontrado")
        
        with open(config_file_path, "r", encoding="utf-8") as f:
            return json.load(f)

        

