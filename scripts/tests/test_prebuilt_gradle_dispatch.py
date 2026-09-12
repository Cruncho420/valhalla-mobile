"""Run the exact production onlyIf closure in standalone Gradle, without Android/native builds.
GRADLE_EXECUTABLE may select an already-installed Gradle 8.x binary; never downloads tools.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / "android/valhalla/build.gradle.kts").read_text()
start = source.index("        onlyIf {")
opening = source.index("{", start)
end, depth = opening + 1, 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
closure = source[start:end]
executable = os.environ.get("GRADLE_EXECUTABLE")
if executable is None:
    installed = sorted((Path.home() / ".gradle/wrapper/dists/gradle-8.2-bin").glob("*/gradle-8.2/bin/gradle"))
    if len(installed) != 1:
        raise RuntimeError("Set GRADLE_EXECUTABLE to an installed Gradle 8.x binary")
    executable = str(installed[0])
with tempfile.TemporaryDirectory(prefix="prebuilt-gradle-") as temporary:
    root = Path(temporary)
    native = root / "native root"
    (native / "scripts").mkdir(parents=True)
    (native / "scripts/verify_native_prebuilt.py").write_text(
        'import argparse,sys\np=argparse.ArgumentParser();p.add_argument("--repo");p.add_argument("--abi");'
        'a=p.parse_args();sys.exit({"verified":0,"missing":10,"invalid":1}[a.abi])\n')
    (root / "settings.gradle.kts").write_text('rootProject.name = "prebuilt-dispatch"\n')
    escaped = str(native).replace("\\", "\\\\").replace('"', '\\"')
    build = 'listOf("verified", "missing", "invalid").forEach { arch ->\n'
    build += 'tasks.register<Exec>(arch) {\nworkingDir = file("' + escaped + '")\n'
    build += 'commandLine("bash", "-c", "echo $arch >> invoked.txt")\n' + closure + '\n}\n}\n'
    (root / "build.gradle.kts").write_text(build)
    result = subprocess.run([executable, "--offline", "--no-daemon", "--console=plain", "--stacktrace",
                             "-Dorg.gradle.jvmargs=-Xmx256m", "verified", "missing", "invalid"],
                            cwd=root, capture_output=True, text=True, timeout=180)
    print(result.stdout + result.stderr)
    assert result.returncode != 0
    assert "Native prebuilt provenance verification failed for invalid" in result.stderr
    assert (native / "invoked.txt").read_text() == "missing\n"
    assert "> Task :verified SKIPPED" in result.stdout
    print("PASS actual Gradle closure: verified skipped, absent ran, rejected never ran")
