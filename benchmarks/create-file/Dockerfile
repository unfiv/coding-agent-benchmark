# Same base image and the same promptfoo / Claude Code versions as in the heavy
# test, but without the C++ toolchain (cmake/ninja/build-essential) - the
# "write a file" task doesn't need it, so the image builds faster.
FROM node:22.22.0-slim@sha256:9b60daf2e7eac9aeb292df6dc9bd12787919a52b5d54e99cb12a81b3713c0f1d

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g promptfoo@0.123.0 && npm cache clean --force

# the native curl installer gets a 403 inside Docker builds (anthropics/claude-code#36306)
RUN npm install -g @anthropic-ai/claude-code@2.1.269 && npm cache clean --force

WORKDIR /app

# Claude Code refuses --dangerously-skip-permissions under root
USER node