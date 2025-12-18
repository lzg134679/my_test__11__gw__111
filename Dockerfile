FROM python:3.12.11-slim-bookworm AS build

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SET_CONTAINER_TIMEZONE=true
ENV CONTAINER_TIMEZONE=Asia/Shanghai
ENV TZ=Asia/Shanghai 


ARG TARGETARCH
ARG VERSION
ENV VERSION=${VERSION}
ENV PYTHON_IN_DOCKER='PYTHON_IN_DOCKER'

COPY scripts/* /app/
WORKDIR /app

RUN apt-get --allow-releaseinfo-change update \
    && apt-get install -y --no-install-recommends jq firefox-esr tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/*  \
    && apt-get clean

COPY ./requirements.txt /tmp/requirements.txt

RUN mkdir /data \
    && cd /tmp \
    && python3 -m pip install --upgrade pip \
    && PIP_ROOT_USER_ACTION=ignore pip install \
    --disable-pip-version-check \
    --no-cache-dir \
    -r requirements.txt \
    && rm -rf /tmp/* \
    && pip cache purge \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/log/*

ENV LANG=C.UTF-8

# Install geckodriver directly from Mozilla releases to avoid missing Debian package
RUN set -eux; \
    if [ "$TARGETARCH" = "arm64" ]; then ARCH="linux-aarch64"; elif [ "$TARGETARCH" = "amd64" ]; then ARCH="linux64"; else echo "Unsupported arch: $TARGETARCH" && exit 1; fi; \
    GECKO_VERSION="0.35.0"; \
    URL="https://github.com/mozilla/geckodriver/releases/download/v${GECKO_VERSION}/geckodriver-v${GECKO_VERSION}-${ARCH}.tar.gz"; \
    curl -fsSL --retry 5 --retry-delay 2 "$URL" -o /tmp/geckodriver.tgz; \
    tar -xzf /tmp/geckodriver.tgz -C /usr/local/bin; \
    rm /tmp/geckodriver.tgz; \
    chmod +x /usr/local/bin/geckodriver; \
    geckodriver --version

CMD ["python3","main.py"]
