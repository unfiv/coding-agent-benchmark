FROM node:22-slim

# Install git and curl
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt-lists/*

# Prevent Puppeteer/Playwright from downloading heavy browser binaries
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Install Promptfoo and immediately clean up npm cache
RUN npm install -g promptfoo --omit=dev && \
    npm cache clean --force

WORKDIR /app

CMD ["promptfoo", "eval"]