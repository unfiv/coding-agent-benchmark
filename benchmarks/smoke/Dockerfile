# Same pinned base image and promptfoo version as the agent benchmarks.
FROM node:22.22.0-slim@sha256:9b60daf2e7eac9aeb292df6dc9bd12787919a52b5d54e99cb12a81b3713c0f1d

# Install git and curl
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Prevent Puppeteer/Playwright from downloading heavy browser binaries
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Install Promptfoo and immediately clean up npm cache
RUN npm install -g promptfoo@0.123.0 --omit=dev && \
    npm cache clean --force

WORKDIR /app

CMD ["promptfoo", "eval"]