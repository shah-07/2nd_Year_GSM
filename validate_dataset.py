#!/usr/bin/env python3
"""Check that the GSM dataset is complete and consistent.

Run from anywhere:   python scripts/validate_dataset.py
Needs:               pip install pillow
Exit code is 1 if any ERROR is found (warnings do not fail the check).
"""
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov"}
IGNORED = {".ds_store", "thumbs.db", "desktop.ini"}
EXIF_MODEL = 272

errors, warnings = [], []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def rel(path):
    return path.relative_to(ROOT).as_posix()


def files_in(folder, exts):
    """Return (files with a wanted extension, stray files), skipping OS junk."""
    ok, stray = [], []
    for p in sorted(folder.iterdir()):
        if p.name.lower() in IGNORED:
            continue
        (ok if p.suffix.lower() in exts else stray).append(p)
    return ok, stray


def read_labels():
    path = ROOT / "labels.csv"
    if not path.exists():
        err("labels.csv is missing")
        return {}
    labels = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            fid = (row.get("fabric_id") or "").strip()
            try:
                gsm = float(row.get("gsm", ""))
            except ValueError:
                err(f"labels.csv line {line}: gsm is not a number ({row.get('gsm')!r})")
                continue
            if fid in labels:
                err(f"labels.csv line {line}: duplicate fabric_id {fid}")
            labels[fid] = gsm
    return labels


def image_info(path):
    """Return (width, height, camera model), or None if the file cannot be read."""
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            model = str(im.getexif().get(EXIF_MODEL) or "unknown")
            model = model.replace("\x00", "").strip() or "unknown"
            return im.width, im.height, model
    except Exception as exc:  # corrupt or truncated file
        err(f"{rel(path)}: cannot be read ({exc})")
        return None


def check_fabric_folders(labels):
    """Check rgb/, ir/ and video/: one folder per labelled fabric, sane contents."""
    counts = defaultdict(dict)
    setups = defaultdict(lambda: defaultdict(Counter))  # group -> (w, h, model) -> {folder: n}
    for mod, exts in (("rgb", IMAGE_EXT), ("ir", IMAGE_EXT), ("video", VIDEO_EXT)):
        base = ROOT / mod
        if not base.is_dir():
            err(f"folder {mod}/ is missing")
            continue
        on_disk = {p.name for p in base.iterdir() if p.is_dir()}
        for fid in sorted(set(labels) - on_disk):
            err(f"{mod}/{fid} is missing (listed in labels.csv)")
        for fid in sorted(on_disk - set(labels)):
            err(f"{mod}/{fid} has no row in labels.csv")
        for fid in sorted(on_disk & set(labels)):
            ok, stray = files_in(base / fid, exts)
            counts[mod][fid] = len(ok)
            if not ok:
                err(f"{mod}/{fid} contains no {'/'.join(sorted(exts))} files")
            for p in stray:
                warn(f"stray file {rel(p)}")
            if mod == "video" and len(ok) > 1:
                warn(f"video/{fid} has {len(ok)} videos (expected 1)")
            if mod != "video":
                for p in ok:
                    info = image_info(p)
                    if info:
                        setups[mod][info][fid] += 1
        common = Counter(counts[mod].values()).most_common(1)
        if common:
            usual = common[0][0]
            for fid, n in sorted(counts[mod].items()):
                if n != usual:
                    warn(f"{mod}/{fid} has {n} files, most fabrics have {usual}")
    return counts, setups


def check_reference(setups):
    """Check reference/dark and reference/ir_no_fabric."""
    for name in ("dark", "ir_no_fabric"):
        folder = ROOT / "reference" / name
        if not folder.is_dir():
            warn(f"reference/{name}/ is missing")
            continue
        ok, stray = files_in(folder, IMAGE_EXT)
        if not ok:
            warn(f"reference/{name}/ has no images")
        for p in stray:
            warn(f"stray file {rel(p)}")
        for p in ok:
            info = image_info(p)
            if info:
                setups[f"reference/{name}"][info][name] += 1


def report_setups(setups):
    """Flag mixed cameras/sizes, and references that do not match the IR photos."""
    for group, combos in sorted(setups.items()):
        if len(combos) > 1:
            warn(f"{group}: images come from {len(combos)} different camera/size combinations")
            for (w, h, model), folders in sorted(combos.items(), key=lambda kv: -sum(kv[1].values())):
                where = ", ".join(sorted(folders))
                warn(f"    {model} {w}x{h}: {sum(folders.values())} images ({where})")
    ir_sizes = {(w, h) for (w, h, _model) in setups.get("ir", {})}
    for group, combos in sorted(setups.items()):
        if not group.startswith("reference/") or not ir_sizes:
            continue
        for (w, h, model), folders in combos.items():
            if (w, h) not in ir_sizes:
                n = sum(folders.values())
                sizes = ", ".join(f"{a}x{b}" for a, b in sorted(ir_sizes))
                warn(f"{group}: {n} images are {w}x{h}, fabric IR photos are {sizes} "
                     f"(rotate or retake before using them as a reference)")


def check_gsm_spread(labels):
    if len(labels) < 2:
        return
    values = list(labels.values())
    mean = sum(values) / len(values)
    spread = max(values) - min(values)
    if mean and spread / mean < 0.20:
        warn(f"gsm only ranges {min(values):g}-{max(values):g} ({spread / mean:.1%} of the mean); "
             f"a model has very little to learn from")


def main():
    labels = read_labels()
    counts, setups = check_fabric_folders(labels)
    check_reference(setups)
    report_setups(setups)
    check_gsm_spread(labels)

    print(f"{'fabric':<12}{'gsm':>7}{'rgb':>6}{'ir':>5}{'video':>7}")
    for fid in sorted(labels):
        print(f"{fid:<12}{labels[fid]:>7g}{counts['rgb'].get(fid, 0):>6}"
              f"{counts['ir'].get(fid, 0):>5}{counts['video'].get(fid, 0):>7}")

    print(f"\n{len(warnings)} warning line(s), {len(errors)} error(s)")
    for w in warnings:
        print(f"  WARN   {w}")
    for e in errors:
        print(f"  ERROR  {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
