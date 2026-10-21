# ATV Leve com Python, adb e o painel já instalados.
#   docker compose up --build          -> painel em http://localhost:8765
#   docker compose run --rm atvleve doctor --ip 192.168.0.20
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends adb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"
COPY profiles ./profiles

# Sem root. /dados guarda os backups; ~/.android guarda a chave adb, para a TV não
# pedir autorização de novo a cada vez que o container é recriado.
RUN useradd --create-home --uid 1000 atv \
    && mkdir -p /dados /home/atv/.android \
    && chown atv:atv /dados /home/atv/.android
USER atv

ENV HOME=/home/atv \
    ATVLEVE_HOME=/dados \
    ATVLEVE_PROFILES=/app/profiles \
    PYTHONUNBUFFERED=1
VOLUME ["/dados", "/home/atv/.android"]
EXPOSE 8765

ENTRYPOINT ["atvleve"]
# O painel escuta em 0.0.0.0 dentro do container, mas só responde a "localhost": a porta
# é publicada apenas em 127.0.0.1 no compose.yaml.
CMD ["web", "--host", "0.0.0.0", "--local-only"]
