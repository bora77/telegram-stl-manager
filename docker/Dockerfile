# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM golang:1.25.8-bookworm AS cli
ARG TARGETARCH
WORKDIR /build
COPY app/.tools/tdl-stl/ .
WORKDIR /build/src
RUN CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH GOMAXPROCS=2 GOMEMLIMIT=2GiB \
    go build -p 1 -trimpath -o /tdl .

FROM python:3.12.14-slim-bookworm
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils util-linux tzdata fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN case "$TARGETARCH" in \
      amd64) archive=x64; digest=dc99eff5008f1ab79bd7084c68513701547a808a89502bf4133683535ab3c695 ;; \
      arm64) archive=arm64; digest=2389ba20e4d8295e8709c20b6263b69bd1ec4972fe38a04ad7a1badbf595b996 ;; \
      *) exit 1 ;; esac \
    && curl --fail --location --output /tmp/7zip.tar.xz "https://github.com/ip7z/7zip/releases/download/26.03/7z2603-linux-${archive}.tar.xz" \
    && echo "$digest  /tmp/7zip.tar.xz" | sha256sum -c - \
    && mkdir /opt/7zip && tar -xJf /tmp/7zip.tar.xz -C /opt/7zip \
    && ln -s /opt/7zip/7zz /usr/local/bin/7zz && rm /tmp/7zip.tar.xz
RUN pip install --no-cache-dir Pillow==11.3.0
WORKDIR /opt/app
COPY app/ ./
COPY --from=cli /tdl .tools/tdl-stl/tdl
RUN python -c 'import hashlib,json,pathlib; p=pathlib.Path(".tools/tdl-stl"); (p/"build.json").write_text(json.dumps({"service_protocol":1,"binary_sha256":hashlib.sha256((p/"tdl").read_bytes()).hexdigest()}))' \
    && ln -s /state/data data && ln -s /downloads downloads
COPY entrypoint.sh /usr/local/bin/stl-entrypoint
RUN chmod +x /usr/local/bin/stl-entrypoint
ENV HOME=/state/home PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 TELEGRAM_STL_BIND=0.0.0.0
EXPOSE 6093
HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6093/api/setup',timeout=2).close()"
ENTRYPOINT ["stl-entrypoint"]
