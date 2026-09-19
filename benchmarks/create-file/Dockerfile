# Тот же базовый образ и те же версии promptfoo / Claude Code, что в тяжёлом
# тесте, но без C++ тулчейна (cmake/ninja/build-essential) - для задачи
# "напиши файл" он не нужен, образ собирается быстрее.
FROM node:22.22.0-slim@sha256:9b60daf2e7eac9aeb292df6dc9bd12787919a52b5d54e99cb12a81b3713c0f1d

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g promptfoo@0.123.0 && npm cache clean --force

# нативный curl-инсталлятор 403-ит внутри Docker-сборок (anthropics/claude-code#36306)
RUN npm install -g @anthropic-ai/claude-code@2.1.269 && npm cache clean --force

WORKDIR /app

# --dangerously-skip-permissions у Claude Code запрещён под root
USER node