FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/Detroit
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY config.yaml /app/config.yaml
COPY start.py /app/start.py
COPY app /app/app

EXPOSE 9990

CMD ["python", "/app/start.py"]
