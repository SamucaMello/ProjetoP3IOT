FROM python:3.12-trixie

#p debuggar melhor
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

CMD ["python", "app.py"]