"""Read a fixed release's wheel metadata in memory; never install or import it."""
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile


def fetch(url, limit):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CodePilot-static-review"}), timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Metadata input too large")
    return raw


def collect(destination):
    metadata_url = "https://pypi.org/pypi/requests/2.32.5/json"
    release = json.loads(fetch(metadata_url, 2_000_000))
    wheel = next(item for item in release["urls"] if item["filename"] == "requests-2.32.5-py3-none-any.whl")
    if not wheel["url"].startswith("https://files.pythonhosted.org/"):
        raise ValueError("Unexpected wheel source")
    raw = fetch(wheel["url"], 2_000_000)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != wheel["digests"]["sha256"]:
        raise ValueError("Wheel did not match the release digest")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entry = archive.getinfo("requests-2.32.5.dist-info/METADATA")
        if entry.file_size > 256_000:
            raise ValueError("Wheel metadata exceeds limit")
        with archive.open(entry) as stream:
            metadata = BytesParser().parsebytes(stream.read(256_001))
    if metadata["Name"].lower() != "requests" or metadata["Version"] != "2.32.5":
        raise ValueError("Unexpected wheel metadata identity")
    result = {"package": "requests", "version": "2.32.5", "metadata_source": metadata_url,
              "wheel": {"filename": wheel["filename"], "url": wheel["url"], "bytes": len(raw), "sha256": digest,
                        "matches_pypi_digest": True},
              "requires_python": metadata["Requires-Python"], "requires_dist": metadata.get_all("Requires-Dist", []),
              "transitive_dependencies_resolved": False, "dependency_image_verified": False,
              "package_installed": False, "package_code_executed": False, "repository_execution_enabled": False}
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    collect(parser.parse_args().report)
