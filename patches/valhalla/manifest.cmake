# Reviewed Valhalla core source identity; parsed as data by artifact tooling.
set(expected_core "e2f017b16080f49203de245a211b09efab09cf72")
# Ordered patch series: file name under patches/valhalla, sha256 of its bytes. Applied in this order.
list(APPEND core_patches "0001-meili-stateful-terminal-segment.patch" "3772e63911228090dd7d7f1217e3fa2aac8700d14ce1fc578de06c60ebc1491e")
list(APPEND core_patches "0002-meili-bounded-topk.patch" "a7700f3fc009b413a5afdcc9d7fae43670624816137429a36170d191e6e2a86d")
# Every core file the series changes: path, sha256 before the series, sha256 after it.
list(APPEND core_sources "src/exceptions.cc" "80428c0f4dfe8e6847ed82a19844c97fcfc94622d3e1dd22f641178c8f28fe7d" "6e8b7c977ab131511b596f54793ac9444cd94e09ef4ed8ea09e9c2c22eb640e3")
list(APPEND core_sources "src/meili/config.cc" "6e35c4a6de63ec0d577332a563850a4d735eed4a712f7deaf5e60681fc1a826b" "2edece25d9161c4b7c5d096d6632a7859084a172da0849df23735f4eb5299f0d")
list(APPEND core_sources "src/meili/map_matcher.cc" "cd7f60457edae705aa4f09fe22aba18d2fe7e3688bcfe7558da75e31dd32a7e9" "99407a309247b2855dcba3a2894579946112dc8297613303468596446a73ac87")
list(APPEND core_sources "src/meili/match_route.cc" "3f5c4de79072a313d467c0812cd8da22b55986381dc4304004abd4f351a6927f" "8f8db9a3c5725c4c5239d6f2a3d8ec5846d4847b08c2f755fdbc5a0d65dcd858")
list(APPEND core_sources "src/thor/trace_attributes_action.cc" "1d9d9d3aedb84e34152051d286fc4bb557cb24a71137f68b927d020308994b3a" "49e8442b3d8c457a102f88379870e2202818f0e8eec7f077d8b4daaba45292f0")
list(APPEND core_sources "valhalla/meili/config.h" "da3725c9a4bd6ca79513008428a08388bf64d282ec24711f742c88345452eee7" "19b3c999f0283b6f17a58d0d50acf6690a1819528e4fae4a41ca5869a66f9d29")
