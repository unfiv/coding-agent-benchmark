#!/usr/bin/env bash
set -e

# Target workspace directory inside the container
TARGET_DIR="./workspace/googletest"
REPO_URL="https://github.com/google/googletest.git"
LOCAL_FALLBACK="../../targets/cpp-gtest"
COMMIT_SHA="283c17563fe7a1111cd7f581aa5d541e8baeff2f" # One of the latest usual working commits, nothing special

mkdir -p ./workspace

# Initialize target repository (Fetch from GitHub or fallback to local copy)
if [ ! -d "$TARGET_DIR" ]; then
    echo "Initializing GoogleTest target repository..."
    if git clone $REPO_URL $TARGET_DIR 2>/dev/null; then
        echo "Successfully fetched GoogleTest from GitHub."
        git -C $TARGET_DIR checkout $COMMIT_SHA
    else
        echo "Network unavailable. Falling back to local offline copy from $LOCAL_FALLBACK..."
        cp -r $LOCAL_FALLBACK $TARGET_DIR
    fi
fi

# Build target repository to ensure the toolchain is fully working
cd $TARGET_DIR
cmake -B build -G Ninja -Dgtest_build_tests=ON
cmake --build build