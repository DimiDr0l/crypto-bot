FROM alpine:3.22.1

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apk,sharing=locked \
    apk add --no-cache \
        bash \
        python3 \
        py3-pip

COPY requirements.txt .
COPY bitget/ bitget/
RUN pip3 install -r requirements.txt --break-system-packages
COPY main.py .
ENTRYPOINT [ "python3", "main.py" ]
