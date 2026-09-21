# 1. Pin the exact Node.js LTS version and the Debian distribution (Bookworm).
# The digest is a verified August snapshot (linux/amd64) whose APT repositories are still stable.
FROM node:22.22.0-slim@sha256:9b60daf2e7eac9aeb292df6dc9bd12787919a52b5d54e99cb12a81b3713c0f1d

# 2. Install a minimal C++ toolchain and Python 3.
# --no-install-recommends is required: it cuts gigabytes of unneeded documentation and headers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    git \
    python3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 3. Pin the exact Promptfoo version (no @latest).
RUN npm install -g promptfoo@0.123.0 && npm cache clean --force

# 4. Install the Claude Code CLI (the native curl installer gets a 403 inside Docker builds
# because of a Cloudflare challenge - anthropics/claude-code#36306; npm is a working workaround).
RUN npm install -g @anthropic-ai/claude-code@2.1.269 && npm cache clean --force

WORKDIR /app

# 5. Claude Code flatly refuses --dangerously-skip-permissions under root, so switch
# to the unprivileged user that already exists in the official node image
# (see https://github.com/nodejs/docker-node/blob/main/docs/BestPractices.md).
USER node