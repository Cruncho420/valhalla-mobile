# Upgrading Valhalla

When a new valhalla release comes out at <https://github.com/valhalla/valhalla/releases>.

```sh
# Clean up valhalla submodule (important this is not concurrent.)
git submodule deinit -f src/valhalla
git rm --cached src/valhalla
rm -rf src/valhalla
rm -rf .git/modules/src/valhalla

# Checkout the latest release branch
git submodule add https://github.com/valhalla/valhalla.git src/valhalla
cd src/valhalla && git checkout 3.6.2 # Replace with the latest version tag release

# Install recursive submodules now that the exact version of valhalla is selected.
git submodule update --init --recursive
```

At this point valhalla's src folder has been updated and prepared. 
Now it's time to test if the existing `src/CMakeLists.txt` still builds by running
an iOS and Android build.

## Downstream patch compatibility

Before changing the core revision, review the [downstream patch contract](../../patches/valhalla/README.md).
The common native configuration gate requires the exact reviewed base and patched source.
A new upstream revision requires reviewing, updating, or removing that patch explicitly.
Never bypass a failed configuration by packaging a library left from an earlier build.

## Local build cleanup

Normal build commands preserve existing build directories.
Cleanup requires the explicit `clean` argument and is scoped to the selected platform:
`./build.sh ios clean`, `./build.sh android clean`, or `./build.sh all clean`.
Malformed platform or architecture arguments fail before cleanup or native execution.
Native build failures stop subsequent packaging.

The launcher previously evaluated an unset command variable as a successful cleanup condition.
That removed all build directories even without `clean`.
The cleanup flag is now initialized explicitly, and the obsolete positional-argument cleanup path is removed.
Run `python3 scripts/tests/test_build_orchestrator.py` to verify cache preservation, scoped cleanup, and failure propagation in temporary directories.

## Android packaging verification

The Android installer requires the native library and its adjacent provenance receipt.
It verifies both against the current source and ABI before copying, then verifies the staged pair.
Missing output now fails the command; the previous script only printed a warning and returned success.
Verified source build outputs remain available for incremental reuse.
An invalid source or receipt leaves an existing installed pair intact before publication begins.
The two final file renames are not one atomic transaction; an interrupted publication must fail
subsequent verification before reuse.

Run `python3 -m unittest discover -s scripts/tests -p 'test_*packaging*.py' -v`.
The orchestration tests inject verifier failures, and the integration tests use real Git/source,
receipt, and ELF-header validation with synthetic binaries; they do not execute Android JNI.

## Apple packaging verification

The build job now supplies a verified aggregate archive and adjacent provenance receipt for
each of the three Apple triplets, plus installed headers.
The packaging job must preserve that layout when downloading the three artifacts.
It verifies all inputs against the current checkout before merging the simulator archives.
After Xcode creates the framework, each simulator slice is extracted and compared byte-for-byte
with its verified input; the device archive and copied headers are checked too.
Source and input identities are checked again before publishing the complete local directory.

`native-package.provenance.json` inside the framework records all three original receipts,
compact identities of all input header and output file hashes (excluding the receipt itself).
Every input's installed headers must match the tree identity recorded by its native build job.
This is build and transformation evidence, not a signature or protection against a malicious producer.
Existing framework outputs are preserved and cause failure instead of being deleted implicitly,
including an empty destination created during packaging (Darwin exclusive rename).
Owned temporary output is removed if a transformation or verification fails.
The release workflow has not been dispatched by this change.

Run `python3 -m unittest discover -s scripts/tests -p test_apple_packaging_integration.py -v`
on a Mac with Xcode to exercise real packaging of tiny compiled Mach-O fixtures, input rejection,
and failure cleanup; this does not replace full Valhalla builds or runtime testing.
