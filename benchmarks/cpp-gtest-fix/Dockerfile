# 1. Зажимаем точную LTS версию Node.js и дистрибутива Debian (Bookworm)
# Использован проверенный августовский срез (linux/amd64), где репозитории APT еще стабильны
FROM node:22.22.0-slim@sha256:9b60daf2e7eac9aeb292df6dc9bd12787919a52b5d54e99cb12a81b3713c0f1d

# 2. Ставим минимальный C++ тулчейн и Python3
# --no-install-recommends обязателен: он отсекает гигабайты ненужных документаций и заголовочных файлов
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    git \
    python3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 3. Прибиваем гвоздями точную версию Promptfoo (без @latest)
RUN npm install -g promptfoo@0.123.0 && npm cache clean --force

# 4. Ставим Claude Code CLI (нативный curl-инсталлятор 403-ит внутри Docker-сборок
# из-за Cloudflare-челленджа — anthropics/claude-code#36306, npm — рабочий воркэраунд)
RUN npm install -g @anthropic-ai/claude-code@2.1.269 && npm cache clean --force

WORKDIR /app

# 5. --dangerously-skip-permissions у Claude Code жёстко запрещён под root -
# переключаемся на непривилегированного пользователя, который уже есть в
# официальном node-образе (см. https://github.com/nodejs/docker-node/blob/main/docs/BestPractices.md)
USER node