# Native artifact provenance

## Android transport and AAR boundary

`package_android_native.py combine` requires all four ABI libraries and adjacent receipts,
verifies their source and ABI identities, and re-verifies the exact eight-member ZIP payload.
`install` accepts only that exact member set, rejects duplicate/path/symlink entries, and
verifies all four ABIs before creating any destination.
Existing output ZIPs and JNI destination directories are preserved rather than overwritten.
An interrupted installation can leave owned partial directories; later verification rejects
them, and this fresh-CI-job installer does not claim automatic rollback or retry cleanup.
The workflow downloads into the ignored build directory so transport does not alter source identity.

The `verify-aar` operation accepts byte-identical verified libraries, or—when an explicit
`--strip-tool` is supplied—bytes identical to independently running that tool with
`--strip-unneeded -o OUTPUT INPUT` on the verified original.
Its receipt records every input receipt, packaged AAR identity, tool identity, and per-ABI
identity/strip transformation with input/output hashes and output size.
The tool is invoked through its original basename because NDK `llvm-strip` can be a symlink
to `llvm-objcopy`, which selects behavior using that name; resolved target bytes are hashed.
Input and tool identity are checked again before publishing the receipt.

The release build's verification uses the same explicit NDK as AGP packaging.
Both `ndkPath` and `ndkVersion` (from that NDK's `source.properties`) must be set: path alone
can conflict with AGP's default version, causing its strip stage to copy unstripped bytes.
Stripping remains enabled; failed verification blocks artifact upload.
Runtime-test restoration must still verify the actual packaged native bytes before execution;
the original build receipt alone does not authorize a different stripped binary.

## Runtime test binding

Android CI restores the original verified JNI ZIP, then builds its instrumentation APK.
When `VALHALLA_TRACE_AAR` is supplied, the module registers `verifyValhallaTraceApk` after
APK packaging and as a dependency of the connected-test task.
The verifier independently rechecks the AAR and its downloaded receipt, then requires all four
APK Valhalla libraries to match the packaged AAR bytes before installation can begin.
AGP registers the connected task late, so the dependency uses `tasks.matching().configureEach`.
Import `java.io.File` and use `File` inside Gradle task closures: the Gradle `java`
extension can shadow the package name there. The full project exposed unresolved
`java.io` references that the isolated task fixture did not catch. Validate script
compilation in the actual project as well as the fixture before packaging.
The exact block was compiled and exercised with a tiny genuine APK and a failing verifier
stand-in; the connected task did not run, which verifies ordering rather than native behavior.

The runner retains test APKs until raw response collection by setting
`android.injected.androidTest.leaveApksInstalledAfterRun=true` on that connected invocation.
It discovers the installed target from the instrumentation declaration, captures its trace
directories before emulator teardown, and rejects unreadable/empty/nonregular/oversized evidence.
The test's exact intentionally invalid `{` input remains valid evidence in
`malformed-request.json`; other captured JSON must parse.
Gradle failures retain their status even when evidence collection also fails.
The result checker requires all five selected Android methods to pass exactly once.

iOS CI selects the original trace test and three added evidence methods, supplies explicit
fixture/evidence directories, and requires a passing result for every selected method.
Both native test jobs now gate publication, including a release-mode run; existing test
assertions are preserved, so unresolved contract failures remain blockers.
These workflow changes have not yet been executed against a full Valhalla build.

Run `python3 -m unittest discover -s scripts/tests -p test_android_artifact_transport.py -v`;
these tests use Git and ELF-header fixtures, not executable Android native libraries.

`scripts/native_artifact_provenance.py` binds a native binary to an explicitly expected source identity.
It uses only Python's standard library, Git, and Apple's `lipo`/`otool` for Apple binaries.
Android ABI verification reads a bounded ELF header.
It does not download, rebuild, delete, or substitute an artifact when verification fails.

## Trust boundary

The expected source comes from a trusted pre-build snapshot or a trusted CI wrapper revision plus the checked-in patch manifest.
An artifact receipt cannot serve as its own expected-source snapshot.
Receipts are build attestations, not signatures or proof against a malicious producer that forges both binary and receipt.
The build workflow must run source capture after the core patch gate and before compilation, and emit only after a successful build.

Source identity includes wrapper revision, core revision, patch digest, original/patched source digests, and an exact dirty-state digest.
The utility checks tracked content, modes, staged changes, nonignored untracked files, and recursively initialized Git submodules.
The exact required unstaged core patch is expected, rather than reported as unrelated dirt.
Other local changes remain explicitly dirty, and `--require-clean` rejects them.
Prebuilt reuse under `GITHUB_ACTIONS=true` also requires a clean initialized-core snapshot;
a matching dirty receipt cannot bypass the CI build policy.
Ignored/generated inputs, external VCPKG files, compiler flags, and environment values are not independently enumerated by this source snapshot.
The caller must bind its build configuration and dependency/toolchain version in `--toolchain-id` and its trusted workflow.

Both native build scripts now capture `native-source.json` after configuration and before compilation.
They use `--cmake-cache` to identify the actual configured C++ compiler and include the exact cache SHA-256 in toolchain identity.
The `CI` environment enables `--require-clean`; local builds retain their exact dirty identity.
Android writes the receipt beside `build/android/<abi>/wrapper/wrapper/libvalhalla-wrapper.so`.
Apple aggregates the installed static archives into `build/apple/<triplet>/libvalhalla_all.a` and writes its adjacent receipt.
If no installed archive is newer than the source snapshot, Apple verifies the existing aggregate instead of making stale inputs look fresh by repackaging them.
Freshness uses nanosecond timestamps; failed aggregation preserves the previous aggregate and receipt.

## Build sequence

Run from a checkout whose core has passed the patch gate:

```sh
python3 scripts/native_artifact_provenance.py source \
  --repo . --manifest patches/valhalla/manifest.cmake \
  --output build/source.json --require-clean --replace

# Run the native build here. Do not emit after a failed build.

python3 scripts/native_artifact_provenance.py emit \
  --repo . --manifest patches/valhalla/manifest.cmake \
  --source build/source.json --binary build/libvalhalla-wrapper.so \
  --abi arm64-v8a --compiler /absolute/path/to/clang++ \
  --toolchain-id 'ndk-r29;api26;cxx20;release' \
  --output build/libvalhalla-wrapper.so.provenance.json --require-clean --replace
```

The post-build source must exactly equal the pre-build snapshot.
An output older than its snapshot cannot acquire a new receipt; it can only reuse an existing receipt matching the binary, source, ABI, and compiler.
This preserves verified warm outputs while rejecting unreceipted prebuilt files.
Timestamp checks detect ordinary stale reuse; they are not a security boundary against a producer deliberately changing timestamps.

Supported ABIs are `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`, `arm64-ios`, `arm64-ios-simulator`, `x64-ios-simulator`, and `macos` (arm64).
Apple verification checks both architecture and platform, so a device arm64 library cannot pass as simulator arm64.
A receipt covers the full bytes of its named binary and one ABI assertion; a universal file can have separately verified per-ABI receipts.

## Packaging without the core checkout

The trusted manifest is `patches/valhalla/manifest.cmake`.
Its four anchored `set(...)` lines are parsed strictly as data, never executed as code.
The caller must supply the CI revision from its trusted workflow, not extract it from the receipt:

```sh
python3 scripts/native_artifact_provenance.py verify \
  --binary build/libvalhalla-wrapper.so --abi arm64-v8a \
  --receipt build/libvalhalla-wrapper.so.provenance.json \
  --expected-wrapper-revision "$TRUSTED_CI_REVISION" \
  --manifest patches/valhalla/manifest.cmake --require-clean \
  --expected-toolchain-id 'ndk-r29;api26;cxx20;release'
```

For a local initialized checkout, derive a fresh snapshot and use `--expected-source build/current-source.json`.
Correct dirty warm outputs are usable when their exact dirty identity matches; they are never labelled clean.
Verify original per-ABI inputs before packaging transformations.
Renaming or merging an archive changes its receipt identity and requires separate packaging provenance; an input receipt cannot attest merged bytes.

## Receipt and failure behavior

Schema version 1 has `kind: valhalla-native-artifact`, `source`, `artifact`, and `toolchain`.
`artifact` contains `abi`, `name`, `size`, and SHA-256.
`source.buildSourceIdentity` is the SHA-256 of canonical source fields before adding that identity field.
`toolchain` contains the caller's configuration identifier, resolved compiler name, executable SHA-256, and `--version` output.
Instead of `--compiler`/`--toolchain-id`, `emit --cmake-cache <path>` derives the compiler from one absolute `CMAKE_CXX_COMPILER` entry and adds the cache hash to the identity.
JSON writes are canonical, file-flushed, atomically published, and parent-directory-flushed.
Apple configuration resolves `clang++` through the selected SDK with `xcrun` and passes
it as an explicit `CMAKE_CXX_COMPILER:FILEPATH` cache entry.
Without that entry, Xcode can compile successfully while keeping compiler identity only
in generated CMake metadata, causing subsequent receipt emission to reject the build.
The verifier continues to reject missing or ambiguous compiler cache entries.
Android similarly records the sole selected NDK host compiler explicitly.
It sets `CMAKE_BUILD_TYPE=Release` during configuration: `--config Release` alone
does not select optimization for a single-configuration Makefiles generator.
Without this setting, the wrapper inherited empty build-type flags while the core
defaulted to Release in its own CMake scope.
`--replace` permits an explicit same-kind, same-ABI, same-name receipt update; it does not overwrite symlinks or different ownership.
One build writer must own each receipt path; this is not a cross-process compare-and-swap service.
A directory-flush failure returns failure even if the complete new file is already visible; retry verification or the same-owner write rather than deleting unknown files.

Metadata, source enumeration, subprocess output/time, and binary hashing have explicit bounds.
Every failure exits nonzero with an explanation.
Run the standalone checks with:

```sh
python3 -m unittest discover -s scripts/tests -p test_native_artifact_provenance.py -v
```

## Installed header binding

Android response capture passes the tar command as one direct `sh -c` argument to
`adb exec-out run-as`; it must not embed another pair of quote characters.
The first full arm64 emulator run passed five tests but exposed this capture error:
the extra quotes made the shell treat the whole command as an executable name.
Removing them recovered all 55 response files from retained private test data.
The runner fixture now models observed exec-out argument boundaries and renders its
diagnostic command log with shell escaping; existing assertions are unchanged.

The iOS trace workflow runs `scripts/verify_apple_package.py` before resolving its local SPM target.
Admission derives source authority from the current checkout and CI revision,
checks the embedded output tree, checks both header copies against build authority,
and verifies the device archive and both extracted simulator slices against their input receipts.
Source and package contents are checked again after admission.
Downloads and evidence stay under ignored `build/` so they do not dirty the source authority.
These receipts identify the recorded build; they are not independent producer signatures.

Apple build emission supplies `--headers-dir install/include` after successful installation.
The optional top-level `headers` receipt member contains only `fileCount` and `treeSha256`.
`tree_identity(path)` hashes a canonical JSON map of sorted relative POSIX paths to file SHA-256,
using the same canonical encoding as the receipt utility.
The tree is bounded to 32,768 entries, refuses symlinks and special files, and detects observed
file or directory identity changes during traversal.
Consumers pass `headers_dir` to `verify` (or `--headers-dir` to its CLI) to require this build authority.
A warm archive without an existing matching header-bound receipt cannot acquire one during packaging.
The receipt remains compact for the real 9,805-header installation.
Older receipts remain readable for consumers that do not request header verification.
This binds the installed output available after the build; it does not claim those generated
headers existed before configuration or independently attest external build dependency origins.
