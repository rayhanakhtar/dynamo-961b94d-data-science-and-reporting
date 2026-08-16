# The single container image — built for BOTH the agent run and the verifier (canonical
# TB2: there is no separate tests/Dockerfile). Install every system dependency / package
# the task needs here, plus the verifier's deps (pytest). Do NOT COPY solution/ or tests/
# into this image — Harbor overlays tests/ at /tests only at verify time, so ground truth
# stays out of the agent's reach.
FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39fcc8335d6d74d5502f6df2d30119ff4790ebbb60b364818d5112d9e3e932

# Verifier deps, baked at BUILD time (pinned) so the verifier installs nothing at verify time.
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip install --no-cache-dir --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

# Task input data: the public operational sources and the authoritative
# contract/dictionary. The agent is expected to read all of them (data/
# contains scheduling_events.csv, reservation_adjustments.ndjson,
# clinic_directory.csv, room_directory.csv, and docs/). No expected outputs or
# solution code live here.
COPY data /app/data

# Starter the agent must repair. It ships as a multi-component system under
# /app: a top-level dispatcher and the bin/ components that implement the
# stateful pipeline (ingest -> rebuild -> prepare). The dispatcher performs a
# fresh build and writes the five required artifacts to /app/output.
COPY bin /app/bin
COPY prepare_facts.py /app/prepare_facts.py

# Output directory for the required artifacts, a scratch work area, and a
# persisted-state directory. All are made world-writable so the agent can
# always write regardless of runtime user.
RUN mkdir -p /app/output /app/work /app/state && chmod 1777 /app/output /app/work /app/state

WORKDIR /app
