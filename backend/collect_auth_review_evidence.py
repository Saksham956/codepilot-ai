"""Fetch fixed public evidence as data; never import repository modules."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tomllib
import urllib.request


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "CodePilot-static-review", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Evidence response exceeds limit")
    return raw


def collect(destination):
    api = "https://api.github.com/repos/psf/requests"
    reference = json.loads(fetch(api + "/git/ref/tags/v2.32.5"))
    item = reference["object"]
    while item["type"] == "tag":
        item = json.loads(fetch(api + "/git/tags/" + item["sha"]))["object"]
    if item["type"] != "commit":
        raise ValueError("Reference did not resolve to a commit")
    commit = json.loads(fetch(api + "/git/commits/" + item["sha"]))
    revision = commit["sha"]
    tree = json.loads(fetch(api + "/git/trees/" + commit["tree"]["sha"] + "?recursive=1"))
    if tree.get("truncated"):
        raise ValueError("Evidence tree was truncated")
    blobs = {item["path"]: item["sha"] for item in tree["tree"] if item["type"] == "blob"}
    selected = ["src/requests/auth.py", "src/requests/__version__.py", "setup.py", "pyproject.toml",
                "requirements-dev.txt", "tests/conftest.py", "tests/test_requests.py"]
    if "tests/test_auth.py" in blobs:
        selected.append("tests/test_auth.py")
    evidence = {"repository": "https://github.com/psf/requests", "tag_observed": "v2.32.5",
                "revision": revision, "tree": commit["tree"]["sha"],
                "checked_at_utc": datetime.now(timezone.utc).isoformat(), "files": [],
                "repository_execution_enabled": False, "repository_code_executed": False}
    for name in selected:
        url = f"https://raw.githubusercontent.com/psf/requests/{revision}/{name}"
        raw = fetch(url)
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != blobs[name]:
            raise ValueError("Source blob did not match the pinned tree")
        record = {"path": name, "source_url": url, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                  "git_blob": blob, "git_blob_matches": True}
        if name.endswith(".py"):
            parsed = ast.parse(raw.decode("utf-8"), filename=name)
            if name.endswith("auth.py") and name.startswith("src/"):
                fn = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "_basic_auth_str")
                record["literal_encodings_in_basic_auth"] = [call.args[0].value for call in ast.walk(fn)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "encode"
                    and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)]
            if name == "setup.py":
                record["literal_runtime_requirements"] = []
                for node in parsed.body:
                    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "requires" for target in node.targets):
                        record["literal_runtime_requirements"] = ast.literal_eval(node.value)
            if name.startswith("tests/"):
                nodes = []
                for parent in parsed.body:
                    functions = parent.body if isinstance(parent, ast.ClassDef) else [parent]
                    for function in functions:
                        if isinstance(function, ast.FunctionDef) and function.name.startswith("test_"):
                            mentions_helper = any(isinstance(node, ast.Name) and node.id == "_basic_auth_str" for node in ast.walk(function))
                            if mentions_helper:
                                components = [name] + ([parent.name] if isinstance(parent, ast.ClassDef) else []) + [function.name]
                                nodes.append("::".join(components))
                record["syntax_derived_basic_auth_candidates_not_collected"] = nodes
        elif name == "pyproject.toml":
            record["declared_pytest_options"] = tomllib.loads(raw.decode())["tool"]["pytest"]["ini_options"]
        elif name == "requirements-dev.txt":
            record["declared_development_requirements_unapproved"] = raw.decode().splitlines()
        evidence["files"].append(record)
    destination.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return evidence


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    collect(args.report)
