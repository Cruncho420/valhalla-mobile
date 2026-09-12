#!/bin/bash

# A rejected configuration must never fall through into a stale native build.
set -e

# Check for Xcode command line tools
if ! command -v xcodebuild &> /dev/null; then
    echo "Xcode command line tools not found. Please install Xcode."
    exit 1
fi

if [ -z ${VCPKG_ROOT+x} ]; then
  echo "Please set VCPKG_ROOT"
  exit 1
fi

# Set the path to the toolchains
vcpkg_toolchain_file="$VCPKG_ROOT/scripts/buildsystems/vcpkg.cmake"
vcpkg_triplet_overlay="$(pwd)/triplets"

# Check if the first argument is a valid Apple architecture
if [ "$1" == "arm64-ios" ]; then
    sdk=iphoneos
    system_name=iOS
    min_deployment_target=16.4
    arch=arm64
    vcpkg_target_triplet=arm64-ios
elif [ "$1" == "arm64-ios-simulator" ]; then
    sdk=iphonesimulator
    system_name=iOS
    min_deployment_target=16.4
    arch=arm64
    vcpkg_target_triplet=arm64-ios-simulator
elif [ "$1" == "x64-ios-simulator" ]; then
    sdk=iphonesimulator
    system_name=iOS
    min_deployment_target=16.4
    arch=x86_64
    vcpkg_target_triplet=x64-ios-simulator
elif [ "$1" == "macos" ]; then
    sdk=macosx
    system_name=Darwin
    min_deployment_target=10.14
    arch=arm64 # TODO: Add x86_64 for older macs?
    vcpkg_target_triplet=arm64-osx # TODO: Try the normal one that's not in the community releases.
elif [ "$1" == "tvos" ]; then
    echo "Error tvos not supported yet." # error: 'fork' is unavailable: not available on watchOS, tvOS (& 'execvp')
    exit 1
    sdk=appletvos
    system_name=tvOS
    min_deployment_target=13.0
    arch=arm64
    vcpkg_target_triplet="" # TODO: Make a custom triplet?
elif [ "$1" == "watchos" ]; then
    echo "Error watchos not supported yet." # error: 'fork' is unavailable: not available on watchOS, tvOS (& 'execvp')
    exit 1
    sdk=watchos
    system_name=watchOS
    min_deployment_target=6.0
    arch=arm64
    vcpkg_target_triplet="" # TODO: Make a custom triplet?
elif [ "$1" == "visionos" ]; then
    echo "Error visionos not supported yet."
    exit 1
else
    echo "Error, first argument must be apple platform: iphoneos, iphonesimulator, macos, tvos, watchos, visionos"
    exit 1
fi

repo_root="$(pwd)"
build_dir="$repo_root/build/apple/$vcpkg_target_triplet"
wrapper_dir="$repo_root/src"
provenance="$repo_root/scripts/native_artifact_provenance.py"
provenance_clean=()
if [ "${CI:-false}" != "false" ] && [ -n "${CI:-}" ] && [ "${CI:-}" != "0" ]; then
    provenance_clean=(--require-clean)
fi

# Move to the build directory
mkdir -p "$build_dir"
cd "$build_dir"

# Xcode otherwise keeps compiler identity outside CMakeCache.txt, where receipts cannot bind it.
apple_cxx_compiler=$(xcrun --sdk "$sdk" --find clang++)
test -x "$apple_cxx_compiler"
cmake -DCMAKE_TOOLCHAIN_FILE="$vcpkg_toolchain_file" \
    -DCMAKE_CXX_COMPILER:FILEPATH="$apple_cxx_compiler" \
    -DVCPKG_TARGET_TRIPLET="$vcpkg_target_triplet" \
    -DVCPKG_OVERLAY_TRIPLETS="$vcpkg_triplet_overlay" \
    -DCMAKE_SYSTEM_NAME="$system_name" \
    -DCMAKE_OSX_SYSROOT="$sdk" \
    -DCMAKE_OSX_ARCHITECTURES="$arch" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$min_deployment_target" \
    -DCMAKE_XCODE_ATTRIBUTE_ONLY_ACTIVE_ARCH=NO \
    -S "$wrapper_dir" \
    -B . \
    -G Xcode
python3 "$provenance" source --repo "$repo_root" \
    --manifest "$repo_root/patches/valhalla/manifest.cmake" \
    --output "$build_dir/native-source.json" --replace "${provenance_clean[@]}"
cmake --build . --config Release --target install -- -jobs $(sysctl -n hw.ncpu)

archives=("$build_dir"/install/lib/*.a)
if [ ! -f "${archives[0]}" ]; then
    echo "Native build produced no installed static libraries."
    exit 1
fi
# Bash's timestamp comparison can discard subsecond precision on supported macOS versions.
fresh_archive=$(python3 -c 'import os,sys; stamp=os.stat(sys.argv[1]).st_mtime_ns; print("true" if any(os.stat(p).st_mtime_ns > stamp for p in sys.argv[2:]) else "false")' \
    "$build_dir/native-source.json" "${archives[@]}")
artifact="$build_dir/libvalhalla_all.a"
if [ "$fresh_archive" = "true" ]; then
    # Do not make old, unreceipted inputs look newly built merely by reaggregating them.
    aggregate_temp=$(mktemp "$artifact.pending.XXXXXX")
    trap 'rm -f "$aggregate_temp"' EXIT
    xcrun libtool -static -o "$aggregate_temp" "${archives[@]}"
    mv "$aggregate_temp" "$artifact"
    trap - EXIT
fi
python3 "$provenance" emit --repo "$repo_root" \
    --manifest "$repo_root/patches/valhalla/manifest.cmake" \
    --source "$build_dir/native-source.json" --abi "$1" --binary "$artifact" \
    --headers-dir "$build_dir/install/include" \
    --cmake-cache "$build_dir/CMakeCache.txt" --output "$artifact.provenance.json" \
    --replace "${provenance_clean[@]}"
