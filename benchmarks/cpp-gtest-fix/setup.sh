#!/usr/bin/env bash
set -e

# Target workspace directory inside the container
TARGET_DIR="./workspace/googletest"
REPO_URL="https://github.com/google/googletest.git"
# Offline copy of the SAME commit (BSD-3-Clause, see targets/cpp-gtest/LICENSE);
# compose.yaml mounts it read-only.
LOCAL_FALLBACK="../../targets/cpp-gtest"
COMMIT_SHA="283c17563fe7a1111cd7f581aa5d541e8baeff2f" # One of the latest usual working commits, nothing special

mkdir -p ./workspace

# Initialize target repository (fetch from GitHub at the pinned commit, or fall back to the offline copy)
if [ ! -d "$TARGET_DIR" ]; then
    echo "Initializing GoogleTest target repository..."
    if git clone "$REPO_URL" "$TARGET_DIR" 2>/dev/null; then
        echo "Successfully fetched GoogleTest from GitHub."
        git -C "$TARGET_DIR" checkout "$COMMIT_SHA"
    else
        echo "Network unavailable. Falling back to the bundled offline copy from $LOCAL_FALLBACK..."
        rm -rf "$TARGET_DIR"   # a failed clone may leave a partial directory behind
        cp -r "$LOCAL_FALLBACK" "$TARGET_DIR"
        # The bundled copy has no .git, but compose.yaml resets the workspace with
        # `git reset --hard` before every run, so record a baseline commit here.
        git -C "$TARGET_DIR" init -q
        git -C "$TARGET_DIR" add -A
        git -C "$TARGET_DIR" -c user.name=bench -c user.email=bench@localhost \
            commit -q -m "baseline: bundled googletest copy"
    fi
fi

# Build target repository to ensure the toolchain is fully working
cd "$TARGET_DIR"
cmake -B build -G Ninja -Dgtest_build_tests=ON
cmake --build build
