FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bison \
    flex \
    g++ \
    gcc \
    git \
    libprotobuf-dev \
    libnl-route-3-dev \
    libnl-3-dev \
    make \
    pkg-config \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/google/nsjail.git /tmp/nsjail \
    && cd /tmp/nsjail \
    && make \
    && cp /tmp/nsjail/nsjail /usr/bin/nsjail \
    && chmod 755 /usr/bin/nsjail \
    && rm -rf /tmp/nsjail

RUN useradd -u 10001 -m -s /usr/sbin/nologin sandboxuser

RUN mkdir -p /var/sandbox && \
    chown -R 10001:10001 /var/sandbox && \
    chmod 700 /var/sandbox

COPY server.py /app/server.py
RUN chmod 644 /app/server.py

RUN chown -R root:root /app && \
    chmod 755 /app

ENV SANDBOX_API_KEY=""

USER root

EXPOSE 8000

CMD ["python", "-u", "/app/server.py"]