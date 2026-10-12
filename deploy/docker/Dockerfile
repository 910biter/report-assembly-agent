ARG BASE_IMAGE=report-assembly-agent-base:offline
FROM ${BASE_IMAGE}

USER root
WORKDIR /opt/app
COPY app ./app
COPY run.py ./run.py
COPY web ./web
COPY deploy/docker/entrypoint.sh ./entrypoint.sh
RUN mkdir -p /opt/app/runtime && chown -R ira:ira /opt/app/runtime

USER ira
EXPOSE 8000
CMD ["bash", "/opt/app/entrypoint.sh"]
