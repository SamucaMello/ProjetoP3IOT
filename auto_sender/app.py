#!/usr/bin/env python3
"""
Simulador da Caixa de Captação Inteligente (IoT) - versão para apresentação.

O que mudou em relação ao simulador original:
  * Cenário "roteiro" (padrão): percorre os slides em ciclo
      fluxo normal -> obstrução com poucas doações (falso positivo) -> caixa enche -> esvazia
  * Comandos pelo teclado durante a execução (digite a letra e Enter):
      d = doação | o = obstrução | m = quase cheia | c = caixa cheia | e = esvaziar
      a = pausar/retomar o automático | s = resumo | h = ajuda | q = sair
      (para outra caixa: "d 2", "o 3"...)
  * Várias caixas no mesmo terminal (--caixas 3) para o slide de escala.
  * Obstrução dura 20 s (antes 12 s), então "Fenda Obstruída" fica visível por ~10 s.
  * "Caixa Cheia" fica 12 s na tela antes da caixa ser esvaziada.
  * O campo "status" enviado agora é o que o SENSOR enxerga sozinho (ingênuo):
    bloqueio >= 10 s vira "Caixa Cheia" (o falso positivo do slide 8).
    Quem corrige é o Node-RED (campo status_logico = "Fenda Obstruída").
    Use --status-completo para voltar ao comportamento antigo (Python decide tudo).
  * Saída no terminal mais limpa (só imprime quando algo muda). Use --verboso para ver o JSON.

O formato do JSON publicado é o mesmo de antes, então o Node-RED e o InfluxDB
continuam funcionando sem alteração.
"""
import argparse
import math
import random
import re
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt
from pydantic import BaseModel, Field

LIMITE_OBSTRUCAO = 10.0  # segundos de bloqueio contínuo


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


class Caixa:
    """Uma caixa de doação simulada (um sensor_id)."""

    def __init__(self, client: mqtt.Client, args, sensor_id: str, indice: int):
        self.client = client
        self.args = args
        self.topico = args.topico
        self.sensor_id = sensor_id
        self.capacidade = args.capacidade
        self.indice = indice

        self.lock = threading.RLock()
        self.auto = args.cenario != "manual"
        self.contador = 0
        self.pulso_inicio = 0.0
        self.pulso_fim = 0.0
        self.bloqueio_inicio: Optional[float] = None
        self.bloqueio_fim: Optional[float] = None

        self._em_tick = False
        self._ultima_linha = None
        self._ticks = 0

        fabrica = CENARIOS.get(args.cenario)
        self.driver = fabrica(self) if fabrica else None
        self.proxima_acao = time.monotonic()

    # ------------------------------------------------------------------
    # Ações físicas (chamadas pelo cenário automático ou pelo teclado)
    # ------------------------------------------------------------------
    def doar(self) -> bool:
        with self.lock:
            if self.contador >= self.capacidade:
                self.log("caixa cheia: doação ignorada")
                return False
            agora = time.monotonic()
            self.contador += 1
            self.pulso_inicio = agora
            self.pulso_fim = agora + random.uniform(0.3, 0.8)
            self.log(f"doação registrada ({self.contador}/{self.capacidade})")
            self._publicar_agora()
            return True

    def obstruir(self, duracao: Optional[float] = None):
        with self.lock:
            duracao = duracao or self.args.duracao_obstrucao
            agora = time.monotonic()
            self.bloqueio_inicio = agora
            self.bloqueio_fim = agora + duracao
            self.log(f"OBSTRUÇÃO iniciada por {duracao:.0f}s (com {self.contador}/{self.capacidade} doações)")
            self._publicar_agora()

    def quase_cheia(self):
        with self.lock:
            alvo = math.ceil(self.capacidade * 0.8)
            self.contador = max(self.contador, min(alvo, self.capacidade - 1))
            self.log(f"ocupação ajustada para {self.contador}/{self.capacidade}")
            self._publicar_agora()

    def encher(self):
        with self.lock:
            self.contador = self.capacidade
            self.log(f"CAIXA CHEIA ({self.contador}/{self.capacidade})")
            self._publicar_agora()

    def esvaziar(self):
        with self.lock:
            self.contador = 0
            self.bloqueio_inicio = self.bloqueio_fim = None
            self.pulso_fim = 0.0
            self.log("caixa esvaziada pelo voluntário")
            self._publicar_agora()

    # ------------------------------------------------------------------
    # Estado e status
    # ------------------------------------------------------------------
    def _estado(self, agora: float):
        """Retorna (sensor_interrompido, tempo_de_bloqueio_em_segundos)."""
        if self.bloqueio_inicio is not None and agora >= self.bloqueio_fim:
            self.bloqueio_inicio = self.bloqueio_fim = None
            self.log("obstrução liberada")
        if self.bloqueio_inicio is not None:
            return True, agora - self.bloqueio_inicio
        if agora < self.pulso_fim:
            return True, agora - self.pulso_inicio
        return False, 0.0

    def _status(self, interrompido: bool, tempo: float) -> str:
        cheia = self.contador >= self.capacidade

        if self.args.status_completo:
            # Comportamento do simulador antigo: o Python decide tudo.
            if cheia:
                return "Caixa Cheia"
            if interrompido and tempo >= LIMITE_OBSTRUCAO:
                return "Fenda Obstruída" if self.contador < self.capacidade * 0.8 else "Caixa Cheia"
            if self.contador >= self.capacidade * 0.8:
                return "Quase Cheia"
            return "Normal"

        # Padrão: o que o sensor "enxerga" sozinho. Bloqueio contínuo parece caixa
        # cheia (falso positivo do slide 8). O Node-RED é quem corrige.
        if cheia or (interrompido and tempo >= LIMITE_OBSTRUCAO):
            return "Caixa Cheia"
        return "Normal"

    # ------------------------------------------------------------------
    # Publicação
    # ------------------------------------------------------------------
    def _publicar_agora(self):
        # Dentro do tick, o próprio tick publica no final (evita mensagem duplicada).
        if not self._em_tick:
            self.publicar()

    def publicar(self):
        with self.lock:
            interrompido, tempo = self._estado(time.monotonic())
            payload = TelemetryPayload(
                sensor_id=self.sensor_id,
                timestamp=agora_iso(),
                contador_doacoes=self.contador,
                capacidade=self.capacidade,
                percentual_ocupacao=round(self.contador / self.capacidade * 100, 1),
                sensor_interrompido=interrompido,
                tempo_interrupcao=round(tempo, 2) if interrompido else None,
                status=self._status(interrompido, tempo),
            )
            texto = payload.model_dump_json()
            info = self.client.publish(self.topico, payload=texto, qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"[ERRO] Falha ao publicar ({self.sensor_id}). Código: {info.rc}")
                return
            self._imprimir(payload, texto)

    def _imprimir(self, p: TelemetryPayload, texto: str):
        if self.args.verboso:
            print(f"[MQTT] {self.topico} | {texto}")
            return
        chave = (p.contador_doacoes, p.sensor_interrompido, int(p.tempo_interrupcao or 0), p.status)
        self._ticks += 1
        if chave == self._ultima_linha and self._ticks % 10 != 0:
            return
        self._ultima_linha = chave
        sensor = "BLOQUEADO" if p.sensor_interrompido else "livre"
        tempo = f"{p.tempo_interrupcao:5.1f}s" if p.tempo_interrupcao is not None else "     -"
        print(
            f"[{self.sensor_id}] {p.contador_doacoes:>3}/{p.capacidade} "
            f"({p.percentual_ocupacao:5.1f}%) | sensor: {sensor:<9} {tempo} | status enviado: {p.status}"
        )

    def log(self, msg: str):
        print(f"  >> [{self.sensor_id}] {msg}")

    # ------------------------------------------------------------------
    # Ciclo (chamado a cada --intervalo segundos)
    # ------------------------------------------------------------------
    def tick(self):
        with self.lock:
            agora = time.monotonic()
            self._em_tick = True
            try:
                if self.auto and self.driver is not None and agora >= self.proxima_acao:
                    try:
                        espera = next(self.driver)
                        self.proxima_acao = agora + espera
                    except StopIteration:
                        self.driver = None
            finally:
                self._em_tick = False
            self.publicar()

    def resumo(self) -> str:
        interrompido, tempo = self._estado(time.monotonic())
        estado = f"BLOQUEADO há {tempo:.0f}s" if interrompido else "livre"
        auto = "automático" if self.auto and self.driver is not None else "manual"
        return f"{self.sensor_id}: {self.contador}/{self.capacidade} | sensor {estado} | {auto}"


# ----------------------------------------------------------------------
# Cenários automáticos (geradores: cada "yield" é uma espera em segundos)
# ----------------------------------------------------------------------
def cenario_roteiro(c: Caixa):
    """Segue os slides: fluxo normal -> falso positivo -> enche -> esvazia."""
    pre = max(1, round(c.capacidade * 0.2))  # ~5 de 30, como no slide 8
    if c.indice:
        yield random.uniform(0, 15)  # defasa as caixas extras
    while True:
        c.esvaziar()
        yield 3
        # Fase 1: fluxo normal (slides 4 a 6)
        for _ in range(pre):
            c.doar()
            yield 2
        # Fase 2: falso positivo (slides 7, 8 e 11)
        c.obstruir()
        yield c.args.duracao_obstrucao + 3
        # Fase 3: caixa enchendo até lotar
        while c.contador < c.capacidade:
            c.doar()
            yield random.uniform(0.8, 1.6)
        yield c.args.pausa_cheia


def cenario_doacao(c: Caixa):
    """Só doações normais, sem obstrução."""
    if c.indice:
        yield random.uniform(0, 8)
    while True:
        c.esvaziar()
        yield 2
        while c.contador < c.capacidade:
            c.doar()
            yield random.uniform(1.5, 3.0)
        yield c.args.pausa_cheia


def cenario_obstrucao(c: Caixa):
    """Poucas doações e depois obstruções repetidas (falso positivo)."""
    pre = max(1, round(c.capacidade * 0.17))
    if c.indice:
        yield random.uniform(0, 8)
    c.esvaziar()
    yield 2
    for _ in range(pre):
        c.doar()
        yield 0.7
    yield 2
    while True:
        c.obstruir()
        yield c.args.duracao_obstrucao + 4


def cenario_aleatorio(c: Caixa):
    """Mistura doações e obstruções ao acaso."""
    if c.indice:
        yield random.uniform(0, 8)
    while True:
        if c.contador >= c.capacidade:
            yield c.args.pausa_cheia
            c.esvaziar()
            yield 2
        elif random.random() < 0.18:
            c.obstruir()
            yield c.args.duracao_obstrucao + random.uniform(3, 6)
        else:
            c.doar()
            yield random.uniform(2, 4)


def cenario_cheia(c: Caixa):
    """Alterna entre caixa cheia e caixa vazia."""
    if c.indice:
        yield random.uniform(0, 8)
    while True:
        c.esvaziar()
        yield 4
        c.encher()
        yield c.args.pausa_cheia


CENARIOS = {
    "roteiro": cenario_roteiro,
    "doacao": cenario_doacao,
    "obstrucao": cenario_obstrucao,
    "aleatorio": cenario_aleatorio,
    "cheia": cenario_cheia,
    "manual": None,
}


# ----------------------------------------------------------------------
# Comandos pelo teclado
# ----------------------------------------------------------------------
AJUDA = """
Comandos (digite a letra e Enter; para outra caixa, adicione o número: "d 2"):
  d  doação            o  obstrução (sensor travado)
  m  quase cheia       c  caixa cheia          e  esvaziar
  a  pausa/retoma o automático (todas as caixas)
  s  resumo            h  ajuda                q  sair
"""


def ler_comandos(caixas, parar: threading.Event):
    while not parar.is_set():
        try:
            linha = input()
        except (EOFError, KeyboardInterrupt):
            return
        linha = linha.strip().lower()
        if not linha:
            continue
        m = re.fullmatch(r"([a-z])\s*(\d+)?", linha)
        if not m:
            print("Comando inválido. Digite h para ajuda.")
            continue
        cmd, num = m.group(1), int(m.group(2) or 1)

        if cmd == "q":
            parar.set()
            return
        if cmd == "h":
            print(AJUDA)
            continue
        if cmd == "s":
            for c in caixas:
                print("  " + c.resumo())
            continue
        if cmd == "a":
            novo = not all(c.auto for c in caixas)
            for c in caixas:
                with c.lock:
                    c.auto = novo
                    c.proxima_acao = time.monotonic()
            print(f"  Automático {'LIGADO' if novo else 'PAUSADO'}")
            continue

        if not 1 <= num <= len(caixas):
            print(f"Caixa {num} não existe (há {len(caixas)}).")
            continue
        alvo = caixas[num - 1]
        if cmd == "d":
            alvo.doar()
        elif cmd == "o":
            alvo.obstruir()
        elif cmd == "m":
            alvo.quase_cheia()
        elif cmd == "c":
            alvo.encher()
        elif cmd == "e":
            alvo.esvaziar()
        else:
            print("Comando inválido. Digite h para ajuda.")


# ----------------------------------------------------------------------
# Programa principal
# ----------------------------------------------------------------------
def montar_ids(sensor_id: str, quantidade: int):
    if quantidade == 1:
        return [sensor_id]
    base = re.sub(r"_\d+$", "", sensor_id)
    return [f"{base}_{i:02d}" for i in range(1, quantidade + 1)]


def main():
    parser = argparse.ArgumentParser(description="Simulador MQTT da Caixa de Captação Inteligente")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--porta", type=int, default=1883)
    parser.add_argument("--topico", default="caixa_doacao")
    parser.add_argument("--sensor-id", default="caixa_doacao_01")
    parser.add_argument("--capacidade", type=int, default=30)
    parser.add_argument("--intervalo", type=float, default=1.0, help="segundos entre publicações")
    parser.add_argument("--cenario", choices=list(CENARIOS), default="roteiro")
    parser.add_argument("--caixas", type=int, default=1, help="quantidade de caixas simuladas")
    parser.add_argument("--duracao-obstrucao", type=float, default=20.0, help="segundos de obstrução")
    parser.add_argument("--pausa-cheia", type=float, default=12.0, help="segundos com a caixa cheia")
    parser.add_argument("--status-completo", action="store_true",
                        help="o Python decide o status (comportamento antigo)")
    parser.add_argument("--verboso", action="store_true", help="imprime o JSON de cada publicação")
    parser.add_argument("--usuario", default=None)
    parser.add_argument("--senha", default=None)
    args = parser.parse_args()

    if args.capacidade < 2:
        parser.error("--capacidade precisa ser pelo menos 2")
    if args.caixas < 1:
        parser.error("--caixas precisa ser pelo menos 1")
    if args.duracao_obstrucao <= LIMITE_OBSTRUCAO:
        print(f"[AVISO] --duracao-obstrucao deve ser maior que {LIMITE_OBSTRUCAO:.0f}s "
              "para a 'Fenda Obstruída' aparecer.")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"simulador-{args.sensor_id}",
        protocol=mqtt.MQTTv311,
    )
    if args.usuario:
        client.username_pw_set(args.usuario, args.senha)
    client.on_connect = lambda c, ud, f, rc, p: print(f"[MQTT] Conectado ao broker (código: {rc})")
    client.on_disconnect = lambda c, ud, f, rc, p: print(f"[MQTT] Desconectado (código: {rc}). Tentando reconectar...")
    client.reconnect_delay_set(min_delay=1, max_delay=5)

    parar = threading.Event()
    signal.signal(signal.SIGINT, lambda s, f: parar.set())
    signal.signal(signal.SIGTERM, lambda s, f: parar.set())

    try:
        client.connect(args.broker, args.porta, keepalive=60)
        client.loop_start()
        time.sleep(0.5)

        ids = montar_ids(args.sensor_id, args.caixas)
        caixas = [Caixa(client, args, sid, i) for i, sid in enumerate(ids)]

        print(f"Simulador iniciado | cenário: {args.cenario} | tópico: {args.topico} | "
              f"caixas: {', '.join(ids)}")
        print(AJUDA)
        threading.Thread(target=ler_comandos, args=(caixas, parar), daemon=True).start()

        while not parar.is_set():
            for c in caixas:
                c.tick()
            parar.wait(args.intervalo)
    except ConnectionRefusedError:
        print(f"[ERRO] Não consegui conectar em {args.broker}:{args.porta}. O Mosquitto está rodando?")
    except Exception as exc:
        print(f"[ERRO] {exc}")
    finally:
        client.loop_stop()
        client.disconnect()
        print("Simulação encerrada.")


if __name__ == "__main__":
    main()