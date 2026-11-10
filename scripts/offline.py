"""Fixed offline build/test/package checks for a controlled Linux container."""
from pathlib import Path
import hashlib
import json
import resource
import subprocess
import tarfile
import zipfile

source = Path("/work/project")
out = Path("/work/results"); out.mkdir(exist_ok=True)
excluded = {".git", ".venv", "build", "dist", "__pycache__"}
original = {}
for path in sorted(source.rglob("*")):
    relative = path.relative_to(source)
    if any(part in excluded or part.endswith(".egg-info") for part in relative.parts):
        continue
    if path.is_symlink():
        raise ValueError("Source inventory does not accept symlinks")
    if path.is_file():
        original[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
(out / "source-inventory.json").write_text(json.dumps(original, indent=2) + "\n")
subprocess.run(["python", "-m", "venv", "/work/buildenv"], check=True)
python = "/work/buildenv/bin/python"
subprocess.run([python, "-m", "pip", "install", "--no-index", "--require-hashes",
    "--find-links=/opt/prepared/wheelhouse", "-r", str(source / "locks/requirements.lock")], check=True)
subprocess.run([python, "-m", "build", "--no-isolation", "--outdir", str(out / "dist")], cwd=source, check=True)
wheel = next((out / "dist").glob("*.whl"))
subprocess.run([python, "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], check=True)
subprocess.run([python, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=source, check=True)
subprocess.run([python, "-I", "-m", "tabular_drift_audit", "demo", "--assert"], cwd="/work", check=True)
sdist = next((out / "dist").glob("*.tar.gz"))
with tarfile.open(sdist) as archive:
    inventory = {item.name.partition("/")[2]: hashlib.sha256(archive.extractfile(item).read()).hexdigest()
                 for item in archive.getmembers() if item.isfile()}
for name, digest in original.items():
    if inventory.get(name) != digest:
        raise ValueError("Source distribution mismatch: " + name)
with zipfile.ZipFile(wheel) as archive:
    wheel_inventory = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                       if not name.endswith("/")}
    for name, digest in wheel_inventory.items():
        if name.startswith("tabular_drift_audit/") and name.endswith(".py"):
            if original.get("src/" + name) != digest:
                raise ValueError("Wheel source mismatch: " + name)
record = {"source_sha256": original, "generated_sdist_members": sorted(set(inventory) - set(original)),
          "sdist_inventory": inventory, "wheel_inventory": wheel_inventory,
          "artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (out / "dist").iterdir()},
          "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
          "scope": "Observed cooperative local container; not a universal native-memory bound."}
(out / "evidence.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps({"status": "OFFLINE_TESTS_PACKAGE_INVENTORY_COMPLETE", "artifacts": record["artifacts"]}))
