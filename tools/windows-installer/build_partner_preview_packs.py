"""Build Windows x64 offline .wdpartner v1 packages for the Workdesk 1.2 local installer preview."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import file_digest, sha256
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

REPOSITORY = Path(__file__).resolve().parents[2]
FEATURES = REPOSITORY / "src/researchos/workdesk/partners/native_features.json"
NAMES = {"codex": "Codex", "deepseek": "DeepSeek", "qwen": "Qwen", "claude": "Claude",
         "grok": "Grok", "gemini": "Gemini", "kimi": "Kimi", "glm": "GLM"}
MAGIC = b"WDPARTNER\x01"
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


def long_path(source):
    # Some pinned npm trees have Windows paths beyond MAX_PATH.
    raw = str(source)
    if os.name == "nt" and not raw.startswith("\\\\?\\"):
        if raw.startswith("\\\\"):
            return Path("\\\\?\\UNC\\" + raw[2:])
        return Path("\\\\?\\" + raw)
    return source


def bundle_dependencies(root, record):
    packed, files = [], []
    for dep in record.get("dependencies", []):
        checked(dep["id"] == NODE_ID and dep["sha256"] == NODE_BINARY, "Unknown runtime dependency")
        node = Path(dep["path"]).resolve()
        checked(node.name == "node.exe" and node.is_relative_to(root / ".tools"), "Runtime outside component tools")
        prefix = "dependencies/" + NODE_ID + "/"
        license_file = node.with_name("LICENSE")
        node_size, license_size = node.stat().st_size, license_file.stat().st_size
        files.extend([(prefix + "node.exe", node, node_size, NODE_BINARY),
                      (prefix + "LICENSE", license_file, license_size, NODE_LICENSE)])
        packed.append({"id": NODE_ID, "version": dep["version"], "sha256": NODE_BINARY,
                       "bytes": node_size + license_size, "entry": prefix + "node.exe",
                       "license_sha256": NODE_LICENSE})
    return packed, files


def add_member(tar, name, length, stream):
    info = tarfile.TarInfo(name)
    info.size = length
    # TarInfo defaults to zero uid/gid/mtime and empty user/group names.
    tar.addfile(info, stream)
    tar.members.clear()   # this writer never references earlier members by name


def write_package(path, contents):
    with path.open("wb") as out:
        out.write(MAGIC)
        with gzip.GzipFile(filename="", fileobj=out, mode="wb", mtime=0, compresslevel=6) as gz:
            with tarfile.open(mode="w|", fileobj=gz, format=tarfile.PAX_FORMAT,
                              bufsize=65536, copybufsize=262144) as tar:
                for name, source, size, _digest in contents:
                    if isinstance(source, bytes):
                        with io.BytesIO(source) as stream:
                            add_member(tar, name, size, stream)
                    else:
                        with long_path(source).open("rb") as stream:
                            checked(os.fstat(stream.fileno()).st_size == size, "Source size differs: " + name)
                            add_member(tar, name, size, stream)


def verify_package(path, contents):
    # Verify the complete finished container (including metadata and the gzip
    # footer) against the trusted input manifest without extracting to disk.
    with path.open("rb") as source:
        checked(source.read(len(MAGIC)) == MAGIC, "Wrong .wdpartner v1 header")
        with gzip.GzipFile(fileobj=source, mode="rb") as gz:
            with tarfile.open(mode="r|", fileobj=gz, bufsize=65536) as tar:
                for name, _source, size, digest in contents:
                    item = tar.next()
                    checked(item and item.isreg() and item.name == name and item.size == size,
                            "Wrong package entry: " + name)
                    with tar.extractfile(item) as stream:
                        digest_read = sha256(stream.read()) if size < 262144 else file_digest(stream, "sha256")
                        actual = digest_read.hexdigest()
                    checked(actual == digest, "Wrong package file SHA-256: " + name)
                    tar.members.clear()
                checked(tar.next() is None, "Unexpected extra file in package")
                while padding := tar.fileobj.read(65536):
                    checked(not padding.strip(b"\x00"), "Unexpected data after tar end")


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
    target = out / ("Workdesk-Partner-" + release + ".wdpartner")
    dependencies, files = bundle_dependencies(root, record)
    entries = []
    for arg in record["entry"]:
        path = Path(arg).resolve()
        if path.is_relative_to(folder):
            entries.append("component/" + path.relative_to(folder).as_posix())
        else:
            dep_entry, = [value["entry"] for source, value in zip(record["dependencies"], dependencies)
                          if Path(source["path"]).resolve() == path]
            entries.append(dep_entry)

    header = {"format": "wdpartner", "format_version": 1, "schema": 1, "id": id_, "release_id": release,
              "platform": record["platform"], "version": record["version"], "entry": entries,
              "component_sha256": record["sha256"], "dependencies": dependencies}
    contents = []
    for name, data in (("partner.json", serialized(header)), ("component-files.json", serialized(rows))):
        contents.append((name, data, len(data), sha256(data).hexdigest()))
    contents.extend(files)
    for row in rows:
        path = PurePosixPath(row["path"])
        checked(not path.is_absolute() and ".." not in path.parts and ":" not in str(path)
                and "\\" not in str(path), "Invalid component path")
        contents.append(("component/" + row["path"], folder / path, row["bytes"], row["sha256"]))
    names = {item[0] for item in contents}
    checked(len(names) == len(contents) and set(entries) <= names, "Duplicate member or missing entry")

    with tempfile.NamedTemporaryFile(dir=out, suffix=".building", delete=False) as tmp:
        partial = Path(tmp.name)
    try:
        write_package(partial, contents)
        verify_package(partial, contents)
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
    catalog = {"format": "wdpartner", "format_version": 1, "schema": 1, "platform": "windows-amd64", "partners": [results[id_] for id_ in NAMES]}
    with tempfile.NamedTemporaryFile("w", dir=output, suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(catalog, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    Path(tmp.name).replace(output / "catalog.json")


if __name__ == "__main__":
    main()
