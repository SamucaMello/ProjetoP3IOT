import argparse
import random
import signal
import time
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt
from pydantic import BaseModel, Field


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TelemetryPayload(BaseModel):
    sensor_id: str
    timestamp: str
    contador_doacoes: int = Field(ge=0)
    capacidade: int = Field(gt=0)
    percentual_ocupacao: float = Field(ge=0.0, le=100.0)
    sensor_interrompido: bool
    tempo_interrupcao: Optional[float] = None
    status: str


class SimuladorCaixa:
    def __init__(self, client: mqtt.Client, topico: str, sensor_id: str, capacidade: int, intervalo: float, cenario: str):
        self.client = client
        self.topico = topico
        self.sensor_id = sensor_id
        self.capacidade = capacidade
        self.intervalo = intervalo
        self.cenario = cenario
        self.parar = False
        self.resetar_estado()

    def resetar_estado(self):
        """Reinicia os contadores para permitir o loop contínuo da simulação."""
        self.contador_doacoes = 0
        self.sensor_interrompido = False
        self.inicio_bloqueio = None
        self.doacao_contada_neste_bloqueio = False
        self.proximo_evento = time.monotonic() + 2
        self.evento_atual = None
        self.fim_evento = None

    def iniciar_evento(self, tipo: str, duracao: float):
        agora = time.monotonic()
        self.evento_atual = tipo
        self.fim_evento = agora + duracao
        self.sensor_interrompido = True
        self.inicio_bloqueio = agora
        self.doacao_contada_neste_bloqueio = False
        print(f"\n[SIMULAÇÃO] Evento: {tipo} ({duracao:.1f}s)")

    def planejar_evento(self, agora: float):
        if self.evento_atual is not None or agora < self.proximo_evento:
            return

        if self.cenario == "doacao":
            self.iniciar_evento("doacao", random.uniform(0.3, 0.8))
        elif self.cenario == "obstrucao":
            self.iniciar_evento("obstrucao", 12.0)
        elif self.cenario == "cheia":
            self.contador_doacoes = self.capacidade
            self.iniciar_evento("obstrucao", 12.0)
        else:
            if random.random() < 0.18:
                self.iniciar_evento("obstrucao", 12.0)
            else:
                self.iniciar_evento("doacao", random.uniform(0.3, 0.8))

    def calcular_status(self, duracao_bloqueio: float) -> str:
        if self.contador_doacoes >= self.capacidade:
            return "Caixa Cheia"
        if self.sensor_interrompido and duracao_bloqueio >= 10:
            if self.contador_doacoes < self.capacidade * 0.8:
                return "Fenda Obstruída"
            return "Caixa Cheia"
        if self.contador_doacoes >= self.capacidade * 0.8:
            return "Quase Cheia"
        return "Normal"

    def publicar(self, duracao_bloqueio: float):
        payload_obj = TelemetryPayload(
            sensor_id=self.sensor_id,
            timestamp=agora_iso(),
            contador_doacoes=self.contador_doacoes,
            capacidade=self.capacidade,
            percentual_ocupacao=round(self.contador_doacoes / self.capacidade * 100, 1),
            sensor_interrompido=self.sensor_interrompido,
            tempo_interrupcao=round(duracao_bloqueio, 2) if self.sensor_interrompido else None,
            status=self.calcular_status(duracao_bloqueio),
        )

        payload_json = payload_obj.model_dump_json()
        info = self.client.publish(self.topico, payload=payload_json, qos=1)
        
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"[MQTT] {self.topico} | {payload_json}")
        else:
            print(f"[ERRO] Falha ao publicar. Código: {info.rc}")

    def executar(self):
        print(f"Iniciando simulador em loop contínuo para '{self.sensor_id}' no tópico '{self.topico}'...")
        
        while not self.parar:
            self.resetar_estado()
            
            while not self.parar and self.contador_doacoes < self.capacidade:
                agora = time.monotonic()
                self.planejar_evento(agora)

                if self.evento_atual is not None:
                    duracao = agora - self.inicio_bloqueio
                    if self.evento_atual == "doacao" and not self.doacao_contada_neste_bloqueio:
                        self.contador_doacoes = min(self.contador_doacoes + 1, self.capacidade)
                        self.doacao_contada_neste_bloqueio = True

                    self.publicar(duracao)

                    if agora >= self.fim_evento:
                        self.sensor_interrompido = False
                        self.inicio_bloqueio = None
                        self.evento_atual = None
                        self.fim_evento = None
                        self.publicar(0)
                        pausa = random.uniform(3, 6) if self.cenario == "aleatorio" else 3
                        self.proximo_evento = time.monotonic() + pausa
                else:
                    self.sensor_interrompido = False
                    self.publicar(0)

                time.sleep(self.intervalo)

            if not self.parar:
                print("\n[SIMULAÇÃO] Caixa atingiu capacidade máxima! Esvaziando para reiniciar o ciclo...")
                for _ in range(3):
                    if self.parar:
                        break
                    self.publicar(0)
                    time.sleep(2.0)


def main():
    parser = argparse.ArgumentParser(description="Simulador MQTT")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--porta", type=int, default=1883)
    parser.add_argument("--topico", default="caixa_doacao")
    parser.add_argument("--sensor-id", default="caixa_doacao_01")
    parser.add_argument("--capacidade", type=int, default=30)
    parser.add_argument("--intervalo", type=float, default=1.0)
    parser.add_argument("--cenario", choices=["aleatorio", "doacao", "obstrucao", "cheia"], default="aleatorio")
    parser.add_argument("--usuario", default=None)
    parser.add_argument("--senha", default=None)
    args = parser.parse_args()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"simulador-{args.sensor_id}",
        protocol=mqtt.MQTTv311,
    )
    if args.usuario:
        client.username_pw_set(args.usuario, args.senha)

    client.on_connect = lambda c, ud, f, rc, p: print(f"[MQTT] Conectado (Código: {rc})")

    try:
        client.connect(args.broker, args.porta, keepalive=60)
        client.loop_start()
        time.sleep(0.5)

        simulador = SimuladorCaixa(client, args.topico, args.sensor_id, args.capacidade, args.intervalo, args.cenario)
        
        signal.signal(signal.SIGINT, lambda s, f: setattr(simulador, 'parar', True))
        signal.signal(signal.SIGTERM, lambda s, f: setattr(simulador, 'parar', True))
        
        simulador.executar()
    except Exception as exc:
        print(f"[ERRO] {exc}")
    finally:
        client.loop_stop()
        client.disconnect()
        print("Simulação encerrada.")


if __name__ == "__main__":
    main()