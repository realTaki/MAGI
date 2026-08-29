FROM node:20-bookworm

RUN apt-get update \
  && apt-get install --no-install-recommends -y build-essential python3 \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspaces/ts-magi
