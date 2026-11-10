"""Fetch only the pinned official artifact closure; never run a source archive."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import shutil
import sys
import urllib.request
from urllib.parse import urlsplit
import zipfile


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def fetch(item, destination):
    url = urlsplit(item["url"])
    if url.scheme != "https" or url.netloc != "files.pythonhosted.org" or url.username:
        raise ValueError("Artifact URL is outside the audited official origin")
    name = item.get("filename", item.get("wheel"))
    if not name or PurePosixPath(name).name != name or name in {".", ".."}:
        raise ValueError("Invalid artifact filename")
    path = destination / name
    expected_size = item["bytes"]
    if path.exists():
        if path.stat().st_size != expected_size or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Existing artifact does not match its immutable manifest")
        return path
    temporary = path.with_name(path.name + ".part")
    digest = hashlib.sha256(); count = 0
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(item["url"], timeout=60) as response, temporary.open("xb") as output:
        while True:
            block = response.read(65_536)
            if not block:
                break
            count += len(block)
            if count > expected_size:
                raise ValueError("Artifact exceeds its pinned byte length")
            digest.update(block); output.write(block)
    if count != expected_size or digest.hexdigest() != item["sha256"]:
        raise ValueError("Artifact hash/size mismatch")
    temporary.rename(path)
    return path


def main():
    manifest = Path(sys.argv[1]); destination = Path(sys.argv[2])
    for subdirectory in ["wheelhouse", "sources", "notices"]:
        (destination / subdirectory).mkdir(parents=True, exist_ok=True)
    packages = json.loads(manifest.read_text())
    for package in packages:
        wheel = fetch(package, destination / "wheelhouse")
        with zipfile.ZipFile(wheel) as archive:
            for member in package["notice_members"]:
                name = PurePosixPath(member)
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("Unsafe notice path")
                data = archive.read(member)
                if len(data) > 16_777_216:
                    raise ValueError("Unexpected notice size")
                target = destination / "notices" / wheel.name / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        for source in package["source_distributions"]:
            fetch(source, destination / "sources")
    license_file = Path("/usr/local/LICENSE")
    if license_file.is_file():
        shutil.copy2(license_file, destination / "notices/Python-LICENSE")
    for file in Path("/usr/share/doc").glob("*/copyright"):
        target = destination / "notices/base" / file.parent.name / "copyright"
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(file, target)
    if Path("/usr/share/common-licenses").is_dir():
        shutil.copytree("/usr/share/common-licenses", destination / "notices/common-licenses", dirs_exist_ok=True)
    print(json.dumps({"wheel_count": len(packages), "source_count": sum(len(p["source_distributions"]) for p in packages)}))


if __name__ == "__main__":
    main()
