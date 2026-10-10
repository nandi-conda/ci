#!/usr/bin/env python3
"""Keep conda-forge packages mirrored into a prefix.dev channel installable.

conda-forge fixes package metadata after the fact with repodata patches (for
example dropping gtk2's `harfbuzz <11` pin once harfbuzz kept its ABI). Those
patches live in conda-forge's repodata.json, not in the package files, so a raw
copy uploaded to our channel keeps the stale pins and stops solving. This
rewrites each copy's info/index.json to match conda-forge's current record.

  sync-mirror.py --files deps/*          patch local copies before uploading them
  sync-mirror.py --channel nandi-testing re-upload channel copies whose metadata
                                         drifted from conda-forge (needs PREFIX_API_KEY)
Add --dry-run to only report.
"""
import argparse
import bz2
import hashlib
import http.client
import io
import json
import os
import sys
import tarfile
import time
import urllib.request
import zipfile

import zstandard

CF = "https://conda.anaconda.org/conda-forge"
SUBDIRS = ["linux-64", "noarch"]
FIELDS = ["depends", "constrains"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nandi-conda-ci"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.read()
        except (OSError, http.client.HTTPException):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def repodata(base, subdir):
    try:
        raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(get(f"{base}/{subdir}/repodata.json.zst"))).read()
    except Exception:
        raw = get(f"{base}/{subdir}/repodata.json")
    d = json.loads(raw)
    return {**d.get("packages", {}), **d.get("packages.conda", {})}


def wanted(rec):
    return {f: list(rec.get(f) or []) for f in FIELDS}


def rewrite_index(tar_bytes, opener, rec):
    """Return the tarball with info/index.json's depends/constrains replaced."""
    src = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
    out = io.BytesIO()
    dst = tarfile.open(fileobj=out, mode="w:", format=src.format)
    for m in src.getmembers():
        data = src.extractfile(m).read() if m.isfile() else None
        if m.name == "info/index.json":
            index = json.loads(data)
            for f, v in wanted(rec).items():
                if v:
                    index[f] = v
                else:
                    index.pop(f, None)
            data = json.dumps(index, indent=2, sort_keys=True).encode()
            m.size = len(data)
        dst.addfile(m, io.BytesIO(data) if data is not None else None)
    dst.close()
    return opener(out.getvalue())


def patch(path, rec):
    name = os.path.basename(path)
    if name.endswith(".conda"):
        stem = name[: -len(".conda")]
        with zipfile.ZipFile(path) as z:
            members = [(i, z.read(i.filename)) for i in z.infolist()]
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
            for info, data in members:
                if info.filename == f"info-{stem}.tar.zst":
                    tar = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
                    data = rewrite_index(tar, lambda b: zstandard.ZstdCompressor(level=19).compress(b), rec)
                z.writestr(info, data)
        new = out.getvalue()
    else:  # .tar.bz2
        new = rewrite_index(bz2.decompress(open(path, "rb").read()), lambda b: bz2.compress(b, 9), rec)
    with open(path, "wb") as f:
        f.write(new)


def index_of(path):
    name = os.path.basename(path)
    if name.endswith(".conda"):
        stem = name[: -len(".conda")]
        with zipfile.ZipFile(path) as z:
            tar = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(z.read(f"info-{stem}.tar.zst"))).read()
    else:
        tar = bz2.decompress(open(path, "rb").read())
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:") as t:
        return json.load(t.extractfile("info/index.json"))


def upload(channel, path):
    data = open(path, "rb").read()
    req = urllib.request.Request(
        f"https://prefix.dev/api/v1/upload/{channel}?force=true",
        data=data,
        method="POST",
        headers={
            "X-File-Name": os.path.basename(path),
            "X-File-SHA256": hashlib.sha256(data).hexdigest(),
            "Authorization": f"Bearer {os.environ['PREFIX_API_KEY']}",
            "Content-Type": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--work", default="sync-mirror-work")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cf = {s: repodata(CF, s) for s in SUBDIRS}
    changed = 0

    for path in a.files:
        sub = index_of(path).get("subdir")
        rec = cf.get(sub, {}).get(os.path.basename(path))
        if rec is None:
            continue
        if wanted(index_of(path)) != wanted(rec):
            print(f"patch {sub}/{os.path.basename(path)}")
            changed += 1
            if not a.dry_run:
                patch(path, rec)

    if a.channel:
        os.makedirs(a.work, exist_ok=True)
        for sub in SUBDIRS:
            ours = repodata(f"https://prefix.dev/{a.channel}", sub)
            for fn, have in sorted(ours.items()):
                rec = cf[sub].get(fn)
                if rec is None or wanted(have) == wanted(rec):
                    continue
                print(f"resync {sub}/{fn}")
                for f in FIELDS:
                    if wanted(have)[f] != wanted(rec)[f]:
                        print(f"  {f}: {sorted(set(wanted(have)[f]) ^ set(wanted(rec)[f]))}")
                changed += 1
                if a.dry_run:
                    continue
                path = os.path.join(a.work, fn)
                with open(path, "wb") as f:
                    f.write(get(f"{CF}/{sub}/{fn}"))
                patch(path, rec)
                print(f"  uploaded: HTTP {upload(a.channel, path)}")

    print(f"{changed} package(s) {'would change' if a.dry_run else 'synced'} with conda-forge metadata")


if __name__ == "__main__":
    sys.exit(main())
