ARG BASE_IMAGE=python:3.11-slim


FROM $BASE_IMAGE AS base-image
LABEL builder=true

WORKDIR /root/src/
ENV PATH=/root/.local/bin:$PATH

RUN pip3 install --no-cache-dir -U pip
RUN pip3 install --user --no-cache-dir gunicorn==20.1.0

RUN apt-get update && apt-get install -y git
COPY ./src/requirements.txt /root/requirements.txt
COPY ./packages/compute_horde /compute_horde
RUN cd /root && pip3 install --user --no-cache-dir -r requirements.txt

COPY ./src/ /root/src/
COPY ./envs/prod/entrypoint.sh /root/src/
COPY ./envs/prod/gunicorn.conf.py /root/src/
COPY ./envs/prod/celery-entrypoint.sh /root/src/
COPY ./envs/prod/celery-beat-entrypoint.sh /root/src/
COPY ./envs/prod/prometheus-cleanup.sh /root/src/

RUN python3 -m compileall -b -f -q /root/


FROM $BASE_IMAGE AS secondary-image
LABEL builder=false

RUN apt-get update \
  && apt-get install -y wget \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /root/src/
ENV PYTHONUNBUFFERED=1
ENV PATH=/root/.local/bin:$PATH

COPY --from=base-image /root/ /root/

EXPOSE 8000

CMD ["./entrypoint.sh"]