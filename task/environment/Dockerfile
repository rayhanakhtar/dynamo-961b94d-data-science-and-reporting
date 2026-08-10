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

# Task input data: the public scheduling export and the authoritative contract.
# The agent is expected to read both (data/ contains scheduling_events.csv and
# docs/fact_contract.md). No expected outputs or solution code live here.
COPY data /app/data

# Broken starter the agent must repair. It ships as the implementation under /app
# and reads /app/data, writing its two required artifacts to /app/output.
COPY prepare_facts.py /app/prepare_facts.py

# Output directory for the two required artifacts. Made world-writable so the
# agent can always write regardless of runtime user.
RUN mkdir -p /app/output && chmod 1777 /app/output

WORKDIR /app
