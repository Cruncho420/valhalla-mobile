# PURPOSE: Bind both native builds to the reviewed downstream core patch.
# RESPONSIBILITY: Verify source identity, serialize preparation, and reject drift.
# DEPENDENCIES: CMake and Git; the exact initialized Valhalla submodule.
# CONSUMERS: src/CMakeLists.txt and isolated patch-gate regression tests.

find_package(Git REQUIRED)
get_filename_component(_valhalla_mobile_root "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)

function(_valhalla_git output directory)
  execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${directory}" ${ARGN}
    RESULT_VARIABLE result OUTPUT_VARIABLE value ERROR_VARIABLE error
    OUTPUT_STRIP_TRAILING_WHITESPACE)
  if(NOT result EQUAL 0)
    message(FATAL_ERROR "Valhalla patch gate: Git verification failed")
  endif()
  set("${output}" "${value}" PARENT_SCOPE)
endfunction()

function(_valhalla_require_clean_core core target)
  _valhalla_git(staged "${core}" diff --cached --name-only)
  _valhalla_git(changed "${core}" diff --name-only)
  _valhalla_git(metadata "${core}" diff --summary)
  _valhalla_git(untracked "${core}" ls-files --others --exclude-standard)
  if(NOT untracked STREQUAL "")
    message(FATAL_ERROR "Valhalla patch gate: unexpected untracked core files")
  endif()
  if(NOT staged STREQUAL "" OR NOT metadata STREQUAL "")
    message(FATAL_ERROR "Valhalla patch gate: unexpected staged or file-mode changes")
  endif()
  if(NOT changed STREQUAL "" AND NOT changed STREQUAL "${target}")
    message(FATAL_ERROR "Valhalla patch gate: unexpected tracked core changes")
  endif()
endfunction()

function(_valhalla_prepare root)
  include("${root}/patches/valhalla/manifest.cmake")
  set(core "${root}/src/valhalla")
  set(target "src/meili/match_route.cc")
  set(patch "${root}/patches/valhalla/0001-meili-stateful-terminal-segment.patch")
  if(NOT EXISTS "${core}/.git" OR IS_SYMLINK "${core}" OR
      NOT EXISTS "${core}/${target}" OR IS_SYMLINK "${core}/${target}" OR
      NOT EXISTS "${patch}" OR IS_SYMLINK "${patch}")
    message(FATAL_ERROR "Valhalla patch gate: initialized regular source and patch required")
  endif()
  _valhalla_git(lock_path "${core}" rev-parse --git-path rods-core-patch.lock)
  get_filename_component(lock_path "${lock_path}" ABSOLUTE BASE_DIR "${core}")
  # Concurrent architecture configurations share this source tree.
  file(LOCK "${lock_path}" GUARD FUNCTION TIMEOUT 60 RESULT_VARIABLE lock_result)
  if(NOT lock_result STREQUAL "0")
    message(FATAL_ERROR "Valhalla patch gate: could not lock core preparation")
  endif()
  _valhalla_git(head "${core}" rev-parse HEAD)
  _valhalla_git(link "${root}" ls-tree HEAD -- src/valhalla)
  _valhalla_git(index "${root}" ls-files --stage -- src/valhalla)
  if(NOT head STREQUAL expected_core OR
      NOT link STREQUAL "160000 commit ${expected_core}\tsrc/valhalla" OR
      NOT index STREQUAL "160000 ${expected_core} 0\tsrc/valhalla")
    message(FATAL_ERROR "Valhalla patch gate: core HEAD or parent gitlink differs from reviewed pin")
  endif()
  _valhalla_require_clean_core("${core}" "${target}")
  file(SHA256 "${patch}" actual_patch)
  file(SHA256 "${core}/${target}" actual_source)
  if(NOT actual_patch STREQUAL patch_sha)
    message(FATAL_ERROR "Valhalla patch gate: patch checksum mismatch")
  endif()
  if(actual_source STREQUAL original_sha)
    _valhalla_git(checked "${core}" apply --check "${patch}")
    _valhalla_git(applied "${core}" apply "${patch}")
    file(SHA256 "${core}/${target}" actual_source)
  endif()
  if(NOT actual_source STREQUAL patched_sha)
    message(FATAL_ERROR "Valhalla patch gate: core source checksum mismatch")
  endif()
  _valhalla_require_clean_core("${core}" "${target}")
  message(STATUS "Valhalla core: reviewed stateful-terminal patch verified")
  if(NOT CMAKE_SCRIPT_MODE_FILE)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
      "${root}/patches/valhalla/manifest.cmake" "${patch}" "${core}/${target}")
  endif()
endfunction()

_valhalla_prepare("${_valhalla_mobile_root}")
