ARG BASE_IMAGE=report-assembly-agent-base:offline
FROM ${BASE_IMAGE} AS license-builder
USER root
RUN apt-get update && apt-get install -y --no-install-recommends build-essential python3.12-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY tools/license-build-requirements.txt tools/build_license_native.py ./tools/
RUN python -m pip install -r tools/license-build-requirements.txt
COPY app ./app
RUN python tools/build_license_native.py --output /native \
    && mkdir -p /release && cp -a app /release/app \
    && cp /native/app/licensing/* /release/app/licensing/ \
    && rm -f /release/app/licensing/_trust.pyx /release/app/licensing/trusted_keys.json \
        /release/app/licensing/protocol.py /release/app/licensing/service.py /release/app/licensing/storage.py \
    && find /release/app/licensing -type d -name __pycache__ -exec rm -rf {} +

FROM ${BASE_IMAGE}

USER root
WORKDIR /opt/app
COPY --from=license-builder /release/app ./app
COPY run.py ./run.py
COPY web ./web
COPY deploy/docker/entrypoint.sh ./entrypoint.sh
RUN python -c "from app.licensing import _trust, protocol, service, storage; from importlib.machinery import ExtensionFileLoader; assert all(isinstance(m.__loader__, ExtensionFileLoader) for m in (_trust, protocol, service, storage))" \
    && chown -R root:root /opt/app && chmod -R go-w /opt/app \
    && mkdir -p /opt/app/runtime && chown -R ira:ira /opt/app/runtime

USER ira
EXPOSE 8000
CMD ["bash", "/opt/app/entrypoint.sh"]
