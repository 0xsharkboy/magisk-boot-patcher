FROM python:3.13-slim-trixie AS base
RUN apt-get update \
    && apt-get install -y --no-install-recommends erofs-utils e2fsprogs android-sdk-libsparse-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

FROM base AS dev
RUN apt-get update \
    && apt-get install -y --no-install-recommends mkbootimg cpio \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir ".[test]"
ENV PYTHONPATH=/src/src

FROM base AS runtime
WORKDIR /work
ENV MBP_CACHE_DIR=/cache
VOLUME /cache
ENTRYPOINT ["magisk-boot-patcher"]
