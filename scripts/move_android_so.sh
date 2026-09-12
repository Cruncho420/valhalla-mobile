#!/bin/bash
set -euo pipefail

repo_root="$(pwd)"
staging=""
cleanup() {
    if [ -n "$staging" ]; then rm -rf "$staging"; fi
}
trap cleanup EXIT

move_arch() {
    local arch=$1
    local src_dir="build/android/$arch/wrapper/wrapper"
    local dest_dir="android/valhalla/src/main/jniLibs/$arch"
    local binary="libvalhalla-wrapper.so"
    local receipt="$binary.provenance.json"

    if [ ! -f "$src_dir/$binary" ] || [ ! -f "$src_dir/$receipt" ]; then
        echo "Error: native output and provenance are required for $arch" >&2
        return 1
    fi
    python3 "$repo_root/scripts/verify_native_prebuilt.py" \
        --repo "$repo_root" --binary "$repo_root/$src_dir/$binary" --abi "$arch"

    # Stage in the ignored build tree so local source identity does not change.
    # Keep the original build output available for validated incremental builds.
    staging="$(mktemp -d "$repo_root/build/android/$arch/.native-install.XXXXXX")"
    cp "$src_dir/$binary" "$staging/$binary"
    cp "$src_dir/$receipt" "$staging/$receipt"
    python3 "$repo_root/scripts/verify_native_prebuilt.py" \
        --repo "$repo_root" --binary "$staging/$binary" --abi "$arch"
    mkdir -p "$dest_dir"
    mv "$staging/$binary" "$dest_dir/$binary"
    mv "$staging/$receipt" "$dest_dir/$receipt"
    rmdir "$staging"
    staging=""
    echo "Installed verified $arch native library and provenance"
}

# The architectures that exist for android and can be moved.
architectures=("arm64-v8a" "armeabi-v7a" "x86_64" "x86")

# Check if an architecture is provided as an argument
if [ $# -eq 1 ]; then
    # Check if the provided architecture is valid
    if [[ " ${architectures[@]} " =~ " $1 " ]]; then
        move_arch "$1"
    else
        echo "Error: Invalid architecture. Supported architectures are: ${architectures[*]}"
        exit 1
    fi
elif [ $# -eq 0 ]; then
    # If no argument is provided, move all architectures
    for arch in "${architectures[@]}"; do
        move_arch "$arch"
    done
else
    echo "Error: expected zero or one architecture argument" >&2
    exit 1
fi
