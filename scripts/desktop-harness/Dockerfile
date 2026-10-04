# Desktop proof harness for the Rods wrapper (FEAT-090 r6 trace cancel). Runs the phone wrapper's own
# ValhallaActor (src/wrapper) on Linux against the pinned core + patch series, on any graph.
# Context to assemble (not committed: 350 MB of core): src/valhalla = valhalla e2f017b16080 with
# patches/valhalla/0001 + 0002 applied (git apply, in order); src/wrapper = this repo's src/wrapper
# valhalla_actor.{h,cpp} (include/valhalla_actor.h flattened) + scripts/desktop-harness/harness.cpp.
# Run: docker run --rm -v <graph dir>:/graph -v <run dir>:/run <image> harness corpus|polls|cancel ...
# (configs point mjolnir.tile_extract at /graph/graph.tar). Results: Rods doc/specs/mapbox-exit/
# results/L-engine-r5-1004.md section 11.
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -q && apt-get install -y -q --no-install-recommends build-essential cmake git pkg-config \
    libboost-dev libprotobuf-dev protobuf-compiler liblz4-dev zlib1g-dev libcurl4-openssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY src/valhalla /src/valhalla
RUN cmake -S /src/valhalla -B /build -DCMAKE_BUILD_TYPE=Release -DENABLE_DATA_TOOLS=OFF -DENABLE_SERVICES=OFF \
    -DENABLE_TOOLS=OFF -DENABLE_PYTHON_BINDINGS=OFF -DENABLE_TESTS=OFF -DENABLE_BENCHMARKS=OFF -DENABLE_HTTP=OFF \
    -DENABLE_GDAL=OFF -DENABLE_SINGLE_FILES_WERROR=OFF -DENABLE_WERROR=OFF -DBUILD_SHARED_LIBS=OFF \
    && cmake --build /build --target valhalla -j8
COPY src/wrapper /wrapper
RUN g++ -O2 -std=c++20 -pthread -I/wrapper -I/src/valhalla -I/src/valhalla/valhalla -I/build/src -I/build/valhalla \
    -I/src/valhalla/third_party/rapidjson/include -I/src/valhalla/third_party/date/include -I/build \
    /wrapper/harness.cpp /wrapper/valhalla_actor.cpp -o /usr/local/bin/harness \
    /build/src/libvalhalla.a -lprotobuf -llz4 -lz -lcurl -lpthread
