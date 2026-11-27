"""Build Windows x64 offline partner ZIPs for the Workdesk 1.2 local installer preview."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

REPOSITORY = Path(__file__).resolve().parents[2]
FEATURES = REPOSITORY / "src/researchos/workdesk/partners/native_features.json"
NAMES = {"codex": "Codex", "deepseek": "DeepSeek", "qwen": "Qwen", "claude": "Claude",
         "grok": "Grok", "gemini": "Gemini", "kimi": "Kimi", "glm": "GLM"}
# node.exe and its LICENSE match the pinned upstream Windows x64 v24.14.0 ZIP.
NODE_ID = "node-v24.14.0-windows-amd64"
NODE_BINARY = "63c259c81e5d472b5f11c8d506070130cb04a1ecf84b80377a34ed6ec9048088"
NODE_LICENSE = "8efdacdc1cfa3460aeb7fe98e3c54337b971d5da70e6eee292b73b981acb220c"


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def checked(value, message):
    if not value:
        raise ValueError(message)


def directory_manifest(record, root):
    """Recreate the Go owner's ordered tree digest before reading file contents."""
    stored = load(root / ".records" / (record["id"] + ".files.json"))
    rows = [{k: row[k] for k in ("path", "sha256", "bytes")} for row in stored]
    canonical = serialized(rows)
    # encoding/json's default string escaping is part of the release digest.
    for original, escaped in (("<", r"\u003c"), (">", r"\u003e"), ("&", r"\u0026"),
                              ("\u2028", r"\u2028"), ("\u2029", r"\u2029")):
        canonical = canonical.replace(original.encode(), escaped.encode())
    checked(sha256(canonical).hexdigest() == record["sha256"], "Component manifest digest differs")
    total = sum(row["bytes"] for row in rows)
    checked(len(rows) == record["files"] and total == record["bytes"], "Component manifest size differs")
    return rows


def add_file(archive, name, source, expected_hash, expected_size=None):
    metadata = ZipInfo(name)  # stable timestamp, independent of installation time
    metadata.compress_type = ZIP_DEFLATED
    digest = sha256()
    size = 0
    # Some official npm packages contain Windows paths beyond MAX_PATH.
    if os.name == "nt" and not str(source).startswith("\\\\?\\"):
        source = Path("\\\\?\\UNC\\" + str(source)[2:]) if str(source).startswith("\\\\") else Path("\\\\?\\" + str(source))
    with source.open("rb") as inp, archive.open(metadata, "w") as output:
        while data := inp.read(1024 * 1024):
            digest.update(data)
            size += len(data)
            output.write(data)
    checked(digest.hexdigest() == expected_hash, "Hash mismatch for " + name)
    checked(expected_size is None or expected_size == size, "Size mismatch for " + name)
    return size


def bundle_dependencies(archive, root, record):
    packed = []
    for dep in record.get("dependencies", []):
        checked(dep["id"] == NODE_ID and dep["sha256"] == NODE_BINARY, "Unknown runtime dependency")
        node = Path(dep["path"]).resolve()
        checked(node.name == "node.exe" and node.is_relative_to(root / ".tools"), "Runtime outside component tools")
        license_file = node.with_name("LICENSE")
        dep_dir = "dependencies/" + NODE_ID + "/"
        size = add_file(archive, dep_dir + "node.exe", node, NODE_BINARY)
        size += add_file(archive, dep_dir + "LICENSE", license_file, NODE_LICENSE)
        packed.append({"id": NODE_ID, "version": dep["version"], "sha256": NODE_BINARY,
                       "bytes": size, "entry": dep_dir + "node.exe", "license_sha256": NODE_LICENSE})
    return packed


def build_one(root, out, release, facts):
    id_ = facts["partner_id"]
    record = load(root / ".records" / (release + ".json"))
    checked(record["id"] == release and record["component_id"] == id_ and record["managed"]
            and record["state"] == "AVAILABLE" and record["platform"] == "windows-amd64"
            and record["version"] == facts["version"] and release.endswith("-" + record["sha256"][:16]),
            "Wrong component release: " + release)
    folder = Path(record["path"]).resolve()
    checked(folder.parent == root / id_ and folder.name == release[len(id_) + 1:], "Component root mismatch")
    rows = directory_manifest(record, root)
    target = out / ("Workdesk-Partner-" + release + ".zip")
    with tempfile.NamedTemporaryFile(dir=out, suffix=".building", delete=False) as tmp:
        partial = Path(tmp.name)
    try:
        with ZipFile(partial, "w", compression=ZIP_DEFLATED) as archive:
            dependencies = bundle_dependencies(archive, root, record)
            entry = []
            for arg in record["entry"]:
                path = Path(arg).resolve()
                if path.is_relative_to(folder):
                    entry.append("component/" + path.relative_to(folder).as_posix())
                else:
                    dep_entry, = [value["entry"] for source, value in zip(record["dependencies"], dependencies)
                                  if Path(source["path"]).resolve() == path]
                    entry.append(dep_entry)
            header = {"format": "workdesk-partner-zip", "schema": 1, "id": id_, "release_id": release,
                      "platform": record["platform"], "version": record["version"], "entry": entry,
                      "component_sha256": record["sha256"], "dependencies": dependencies}
            archive.writestr(ZipInfo("partner.json"), serialized(header))
            archive.writestr(ZipInfo("component-files.json"), serialized(rows), compress_type=ZIP_DEFLATED)
            for row in rows:
                path = PurePosixPath(row["path"])
                checked(not path.is_absolute() and ".." not in path.parts and ":" not in str(path)
                        and "\\" not in str(path), "Invalid component path")
                add_file(archive, "component/" + row["path"], folder / path, row["sha256"], row["bytes"])
        with ZipFile(partial) as finished:
            checked(finished.testzip() is None, "ZIP CRC failed: " + release)
        with partial.open("rb") as stream:
            package_hash = file_digest(stream, "sha256").hexdigest()
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    result = {"id": id_, "name": NAMES[id_], "version": record["version"], "release_id": release,
              "file": target.name, "bytes": target.stat().st_size, "sha256": package_hash,
              "component_bytes": record["bytes"], "dependencies": dependencies,
              "installed_bytes": record["bytes"] + sum(dep["bytes"] for dep in dependencies)}
    print(id_, result["bytes"], result["installed_bytes"], flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.components.resolve(), args.out.resolve()
    checked(not output.is_relative_to(root), "Package output must be outside the component store")
    output.mkdir(parents=True, exist_ok=True)
    releases = load(FEATURES)["releases"]
    # The previous GLM variant lacked account support. Select the verified release supporting both routes.
    selected = [(r, f) for r, f in releases.items() if set(f["resume"]["routes"]) == {"account", "api"}]
    checked(len(selected) == len(NAMES) and {f["partner_id"] for _, f in selected} == set(NAMES),
            "Expected exactly one dual-interface release for each partner")
    selected.sort(key=lambda item: item[1]["partner_id"] != "deepseek")
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(build_one, root, output, r, f) for r, f in selected]
        for job in as_completed(jobs):
            item = job.result()
            results[item["id"]] = item
    catalog = {"schema": 1, "platform": "windows-amd64", "partners": [results[id_] for id_ in NAMES]}
    with tempfile.NamedTemporaryFile("w", dir=output, suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(catalog, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    Path(tmp.name).replace(output / "catalog.json")


if __name__ == "__main__":
    main()
