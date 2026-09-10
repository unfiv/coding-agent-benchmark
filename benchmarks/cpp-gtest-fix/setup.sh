#!/usr/bin/env bash
set -e

# Target workspace directory inside the container
TARGET_DIR="./workspace/googletest"
REPO_URL="https://github.com/google/googletest.git"
LOCAL_FALLBACK="../../targets/cpp-gtest"

mkdir -p ./workspace

# Initialize target repository (Fetch from GitHub or fallback to local copy)
if [ ! -d "$TARGET_DIR" ]; then
    echo "Initializing GoogleTest target repository..."
    if git clone --depth 1 --branch v1.14.0 $REPO_URL $TARGET_DIR 2>/dev/null; then
        echo "Successfully fetched GoogleTest from GitHub."
    else
        echo "Network unavailable. Falling back to local offline copy from $LOCAL_FALLBACK..."
        cp -r $LOCAL_FALLBACK $TARGET_DIR
    fi
fi

# Build target repository to ensure the toolchain is fully working
cd $TARGET_DIR
cmake -B build -G Ninja -Dgtest_build_tests=ON
cmake --build build