FROM python:3.12-trixie

#p debuggar melhor
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

ENV BROKER_HOST=mosquitto 
ENV BROKER_PORT=1883

CMD python app.py --broker $BROKER_HOST --porta $BROKER_PORT