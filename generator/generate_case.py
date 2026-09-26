"""Synthetic damaged-storage generator for VERDICT (CLAUDE.md §11).

Simulates a simple block allocator on a 2000-block disk: write files, delete
some, write more (fragmentation emerges), then damage the disk.

Two agreed additions to §11 step 4 (the literal steps rarely fragment two PNGs
and never leave a deleted file intact): phase-1 files are separated by 0-3 free
blocks left by earlier use, and 2 more live files are deleted after phase 2.
Writes data/cases/<case>.img and data/truth/<case>.json.

Usage: python generator/generate_case.py --case demo01 --corpus demo --seed 37
"""
import argparse
import datetime
import hashlib
import io
import json
import math
import os
import struct
import sys
import zipfile
import zlib

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.config import BLOCK_SIZE, IDAT_CHUNK_SIZE, WINDOW  # noqa: E402

NUM_BLOCKS = 2000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CORPUS_IDS = {"train": 1, "val": 2, "demo": 3, "docx": 4}  # keeps each corpus in its own seed space


# ---------------------------------------------------------------- PNG writer

def png_chunk(chunk_type, data):
    """One PNG chunk: [4B length][4B type][data][4B CRC32 over type+data]."""
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def write_png(pixels):
    """Hand-written PNG encoder: 8-bit RGB, no interlace, filter 0 on every row."""
    height, width, _ = pixels.shape
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + pixels[row].tobytes() for row in range(height))
    stream = zlib.compress(raw, 9)
    out = PNG_SIGNATURE + png_chunk(b"IHDR", ihdr)
    for start in range(0, len(stream), IDAT_CHUNK_SIZE):
        out += png_chunk(b"IDAT", stream[start:start + IDAT_CHUNK_SIZE])
    return out + png_chunk(b"IEND", b"")


def check_png_opens(png_bytes, pixels):
    """Confirm Pillow decodes our PNG back to exactly the pixels we wrote."""
    decoded = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
    if not np.array_equal(decoded, pixels):
        raise RuntimeError("generated PNG does not round-trip through Pillow")


# ------------------------------------------------------- image content families
# Each function returns a float field in [0, 1] of shape (h, w).

def grid(h, w):
    return np.mgrid[0:h, 0:w].astype(float)


def field_stripes(rng, h, w):
    y, x = grid(h, w)
    angle = rng.uniform(0, math.pi)
    return 0.5 + 0.5 * np.sin((x * math.cos(angle) + y * math.sin(angle)) * rng.uniform(0.04, 0.15))


def field_checker(rng, h, w):
    y, x = grid(h, w)
    size = int(rng.integers(10, 30))
    return ((x // size + y // size) % 2) * 0.8 + 0.1


def field_rings(rng, h, w):
    y, x = grid(h, w)
    radius = np.hypot(y - rng.uniform(0, h), x - rng.uniform(0, w))
    return 0.5 + 0.5 * np.sin(radius * rng.uniform(0.08, 0.25))


def field_waves(rng, h, w):
    y, x = grid(h, w)
    total = np.zeros((h, w))
    for _ in range(3):
        angle = rng.uniform(0, 2 * math.pi)
        total += np.sin((x * math.cos(angle) + y * math.sin(angle)) * rng.uniform(0.03, 0.12))
    return (total + 3) / 6


def field_blobs(rng, h, w):
    y, x = grid(h, w)
    total = np.zeros((h, w))
    for _ in range(int(rng.integers(3, 7))):
        spread = rng.uniform(15, 50)
        total += np.exp(-((y - rng.uniform(0, h)) ** 2 + (x - rng.uniform(0, w)) ** 2) / (2 * spread ** 2))
    return total / total.max()


def field_diamonds(rng, h, w):
    y, x = grid(h, w)
    distance = np.abs(x - rng.uniform(0, w)) + np.abs(y - rng.uniform(0, h))
    return (distance * rng.uniform(0.02, 0.06)) % 1.0


def field_plasma(rng, h, w):
    y, x = grid(h, w)
    a, b, c = rng.uniform(0.02, 0.08, 3)
    total = np.sin(x * a) + np.sin(y * b) + np.sin((x + y) * c) + np.sin(np.hypot(x - w / 2, y - h / 2) * a)
    return (total + 4) / 8


def field_terrain(rng, h, w):
    cells = int(rng.integers(4, 9))
    coarse = rng.uniform(0, 1, (cells + 1, cells + 1))
    ys = np.linspace(0, cells, h)
    xs = np.linspace(0, cells, w)
    y0 = np.minimum(ys.astype(int), cells - 1)
    x0 = np.minimum(xs.astype(int), cells - 1)
    fy = (ys - y0)[:, None]
    fx = (xs - x0)[None, :]
    top = coarse[y0][:, x0] * (1 - fx) + coarse[y0][:, x0 + 1] * fx
    bottom = coarse[y0 + 1][:, x0] * (1 - fx) + coarse[y0 + 1][:, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


def field_spiral(rng, h, w):
    y, x = grid(h, w)
    dy, dx = y - h / 2, x - w / 2
    arms = int(rng.integers(2, 7))
    return 0.5 + 0.5 * np.sin(np.arctan2(dy, dx) * arms + np.hypot(dy, dx) * rng.uniform(0.05, 0.15))


IMAGE_FAMILIES = {
    "train": [field_stripes, field_checker, field_rings],
    "val": [field_waves, field_blobs, field_diamonds],
    "demo": [field_plasma, field_terrain, field_spiral],
    "docx": [field_rings, field_blobs, field_plasma],     # residue only; docx disks hold no PNGs
}


def make_image(rng, corpus):
    """Procedural RGB image: a posterised pattern, a random two-colour ramp and speckle noise.

    Posterising plus sparse speckle keeps PNGs around 3-15 blocks, small enough for BUDGET.
    """
    height, width = int(rng.integers(150, 301)), int(rng.integers(150, 301))
    families = IMAGE_FAMILIES[corpus]
    family = families[int(rng.integers(len(families)))]
    levels = int(rng.integers(12, 40))
    field = np.round(family(rng, height, width) * levels) / levels
    low, high = rng.uniform(0, 255, 3), rng.uniform(0, 255, 3)
    rgb = low + (high - low) * field[..., None]
    speckle = rng.random((height, width)) < rng.uniform(0.03, 0.08)
    rgb[speckle] += rng.normal(0, 25, (int(speckle.sum()), 3))
    return family.__name__.replace("field_", ""), np.clip(rgb, 0, 255).astype(np.uint8)


# -------------------------------------------------------- text content families

def timestamps(rng, count):
    moment = datetime.datetime(2026, 1, 1) + datetime.timedelta(minutes=int(rng.integers(0, 500000)))
    for _ in range(count):
        moment += datetime.timedelta(seconds=int(rng.integers(1, 90)))
        yield moment.strftime("%Y-%m-%d %H:%M:%S")


def log_line(rng, corpus, stamp):
    if corpus == "train":  # web server access log
        path = rng.choice(["/api/items", "/api/users", "/login", "/cart", "/search"])
        status = rng.choice([200, 200, 200, 201, 304, 404, 500])
        return f"{stamp} 192.168.{rng.integers(0, 8)}.{rng.integers(2, 250)} GET {path}/{rng.integers(1, 999)} {status} {rng.integers(3, 900)}ms"
    if corpus == "val":  # authentication log
        user = rng.choice(["root", "admin", "deploy", "backup", "guest"])
        verb = rng.choice(["Accepted password", "Failed password", "Invalid user"])
        return f"{stamp} sshd[{rng.integers(1000, 9999)}]: {verb} for {user} from 10.0.{rng.integers(0, 16)}.{rng.integers(2, 250)} port {rng.integers(30000, 65000)}"
    service = rng.choice(["payments-worker", "ledger-sync", "auth-gateway", "report-builder"])  # demo: app log
    level = rng.choice(["INFO", "INFO", "INFO", "WARN", "ERROR"])
    return f"{stamp} [{service}] {level} batch {rng.integers(1000, 9999)} processed {rng.integers(1, 80)} records in {rng.integers(5, 3000)}ms"


def make_log(rng, corpus, target_size):
    lines = []
    for stamp in timestamps(rng, 100000):
        lines.append(log_line(rng, corpus, stamp))
        if sum(len(line) + 1 for line in lines) >= target_size:
            break
    return ("\n".join(lines) + "\n").encode()


CSV_HEADERS = {
    "train": "sensor_id,zone,temp_c,humidity_pct",
    "val": "txn_id,account,amount,currency",
    "demo": "sku,item,warehouse,qty,unit_price",
    "docx": "sku,item,warehouse,qty,unit_price",   # residue only (rows use the demo format)
}


def csv_row(rng, corpus, index):
    if corpus == "train":
        return f"S{index:04d},{rng.choice(['north', 'south', 'east', 'west'])},{rng.uniform(15, 35):.1f},{rng.uniform(20, 90):.1f}"
    if corpus == "val":
        return f"T{index:06d},AC{rng.integers(10000, 99999)},{rng.uniform(1, 5000):.2f},{rng.choice(['INR', 'USD', 'EUR'])}"
    item = rng.choice(["bolt", "washer", "bearing", "gasket", "valve", "spring"])
    return f"SKU{index:05d},{item},WH{rng.integers(1, 9)},{rng.integers(0, 500)},{rng.uniform(0.5, 90):.2f}"


def make_csv(rng, corpus, target_size):
    lines = [CSV_HEADERS[corpus]]
    index = 0
    while sum(len(line) + 1 for line in lines) < target_size:
        index += 1
        lines.append(csv_row(rng, corpus, index))
    return ("\n".join(lines) + "\n").encode()


# ------------------------------------------------------------------- DOCX writer

DOCX_TOPICS = ["incident review", "vendor audit", "quarterly ledger", "site inspection",
               "access log summary", "shipment reconciliation", "board minutes", "field notes"]
DOCX_WORDS = ("account amount approved archive asset audit balance batch branch budget cargo case "
              "clearance client contract courier custody deadline delivery deposit device dispatch "
              "document entry evidence exception export filing freight handover invoice ledger "
              "manifest memo notice order packet payment permit policy record refund register "
              "release report request review route schedule seal serial settlement shipment "
              "signature statement storage summary supplier tally ticket transfer vault voucher "
              "warehouse witness").split()

DOCX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>'),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '</Relationships>'),
}


def docx_paragraph(rng):
    words = [str(rng.choice(DOCX_WORDS)) for _ in range(int(rng.integers(12, 40)))]
    words.insert(int(rng.integers(len(words))), f"#{int(rng.integers(10000, 99999))}")
    words.insert(int(rng.integers(len(words))), f"{rng.uniform(10, 99999):.2f}")
    return " ".join(words).capitalize() + "."


def write_docx(rng, title, min_size):
    """A minimal DOCX (zipfile, ZIP_DEFLATED) at least `min_size` bytes long.

    Written to a seekable buffer so zipfile never uses data descriptors (flag bit 3 = 0).
    Fixed timestamps and create_system make the bytes depend only on the seed.
    """
    paragraphs = [title.title()]
    while True:
        paragraphs += [docx_paragraph(rng) for _ in range(20)]
        body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
        parts = dict(DOCX_PARTS)
        parts["word/document.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body}</w:body></w:document>')
        parts["docProps/core.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title></cp:coreProperties>')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in parts.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                archive.writestr(info, text)
        data = buffer.getvalue()
        if len(data) >= min_size:
            return data


def check_docx(data):
    """Every entry: flag bit 3 clear, CRC checks out (zipfile.testzip), DOCX parts present."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if any(info.flag_bits & 0x08 for info in archive.infolist()):
            raise RuntimeError("DOCX entry uses a data descriptor")
        if archive.testzip() is not None:
            raise RuntimeError("DOCX entry fails its CRC")
        if not {"[Content_Types].xml", "word/document.xml"} <= set(archive.namelist()):
            raise RuntimeError("DOCX is missing a required part")


def build_docx_corpus(rng):
    """3-4 DOCX files, each >= 12 KB so it spans at least 3 blocks."""
    files = []
    for number in range(1, int(rng.integers(3, 5)) + 1):
        topic = str(rng.choice(DOCX_TOPICS))
        data = write_docx(rng, topic, int(rng.integers(12 * 1024, 24 * 1024)))
        check_docx(data)
        files.append({"name": f"doc_{number:02d}_{topic.replace(' ', '_')}.docx", "type": "docx", "data": data})
    return files


def build_corpus(rng, corpus):
    """6 procedural PNGs, 2 text logs and 1 CSV (docx corpus: 3-4 DOCX files only)."""
    if corpus == "docx":
        return build_docx_corpus(rng)
    files = []
    for number in range(1, 7):
        family, pixels = make_image(rng, corpus)
        png = write_png(pixels)
        check_png_opens(png, pixels)
        files.append({"name": f"img_{number:02d}_{family}.png", "type": "png", "data": png})
    for number in range(1, 3):
        files.append({"name": f"log_{number:02d}.log", "type": "log",
                      "data": make_log(rng, corpus, int(rng.integers(5000, 14000)))})
    files.append({"name": "table_01.csv", "type": "csv", "data": make_csv(rng, corpus, int(rng.integers(4000, 10000)))})
    return files


# ------------------------------------------------------------------- residue

def build_residue_pool(rng, corpus):
    """Leftover bytes of the same kinds of content: compressed image data, raw pixels and text."""
    compressed, raw = [], []
    for _ in range(4):
        _, pixels = make_image(rng, corpus)
        raw.append(pixels.tobytes())
        compressed.append(zlib.compress(pixels.tobytes(), 9))
    text = make_log(rng, corpus, 30000) + make_csv(rng, corpus, 20000)
    return {"compressed": b"".join(compressed), "raw": b"".join(raw), "text": text}


def residue_bytes(rng, pool, length):
    kind = rng.choice(["compressed", "raw", "text"], p=[0.55, 0.2, 0.25])
    source = pool[kind]
    start = int(rng.integers(0, len(source) - length))
    return source[start:start + length]


def residue_block(rng, pool):
    while True:
        block = residue_bytes(rng, pool, BLOCK_SIZE)
        if not block.startswith(PNG_SIGNATURE):  # never plant a fake PNG anchor
            return block


# ---------------------------------------------------------------- disk model

class Disk:
    """2000 blocks of bytes plus the allocator's view of which blocks are free."""

    def __init__(self, rng, pool):
        self.rng = rng
        self.pool = pool
        self.data = bytearray(NUM_BLOCKS * BLOCK_SIZE)
        self.free = [True] * NUM_BLOCKS
        self.owner = [None] * NUM_BLOCKS  # index of the file whose bytes are in the block
        for block in range(NUM_BLOCKS):
            self.put(block, residue_block(rng, pool))

    def put(self, block, chunk):
        self.data[block * BLOCK_SIZE:(block + 1) * BLOCK_SIZE] = chunk

    def get(self, block):
        return bytes(self.data[block * BLOCK_SIZE:(block + 1) * BLOCK_SIZE])

    def first_fit(self, count):
        """Lowest-numbered free blocks, one block at a time (so files can fragment)."""
        chosen = [block for block in range(NUM_BLOCKS) if self.free[block]][:count]
        if len(chosen) < count:
            raise RuntimeError("disk full")
        return chosen

    def sequential(self, count, gap):
        """Contiguous blocks starting `gap` free blocks after the last used block."""
        used = [block for block in range(NUM_BLOCKS) if not self.free[block]]
        start = (used[-1] + 1 if used else 0) + gap
        return list(range(start, start + count))

    def write_file(self, file_index, file, blocks):
        data = file["data"]
        for position, block in enumerate(blocks):
            chunk = data[position * BLOCK_SIZE:(position + 1) * BLOCK_SIZE]
            if len(chunk) < BLOCK_SIZE:  # final-block slack keeps old residue-like bytes
                chunk += residue_bytes(self.rng, self.pool, BLOCK_SIZE - len(chunk))
            self.put(block, chunk)
            self.free[block] = False
            self.owner[block] = file_index
        file["blocks"] = blocks
        file["status"] = "live"

    def delete_file(self, file):
        """Mark blocks free but leave the bytes in place, like a real filesystem."""
        for block in file["blocks"]:
            self.free[block] = True
        file["status"] = "deleted"

    def overwrite(self, block):
        self.put(block, residue_block(self.rng, self.pool))
        self.owner[block] = None


def runs(blocks):
    """Contiguous runs of a block list, e.g. [4, 5, 6, 90, 91] -> [(4, 6), (90, 91)]."""
    result = []
    for block in blocks:
        if result and block == result[-1][1] + 1:
            result[-1] = (result[-1][0], block)
        else:
            result.append((block, block))
    return result


# ------------------------------------------------------------ damage steps

def mark_reused_blocks(disk, files):
    """Deleted files whose blocks were reused by later writes are (partially) overwritten."""
    for index, file in enumerate(files):
        lost = [block for block in file["blocks"] if disk.owner[block] != index]
        file["overwritten_blocks"] = lost
        if file["status"] == "deleted" and len(lost) == len(file["blocks"]):
            file["status"] = "overwritten"
        elif file["status"] == "deleted" and lost:
            file["status"] = "partially_overwritten"


def partially_overwrite_deleted(disk, files, rng):
    """Overwrite the back half of one deleted file that still has its first block."""
    targets = [f for f in files if f["status"] == "deleted" and len(f["blocks"]) >= 2]
    if not targets:
        return None
    pngs = [f for f in targets if f["type"] == "png"]
    target = (pngs or targets)[int(rng.integers(len(pngs or targets)))]
    keep = len(target["blocks"]) // 2
    for block in target["blocks"][keep:]:
        disk.overwrite(block)
    target["overwritten_blocks"] = target["blocks"][keep:]
    target["status"] = "partially_overwritten"
    return target


def file_bytes_in_block(file, position):
    """How many bytes of the file (not slack) live in its position-th block."""
    return min(BLOCK_SIZE, len(file["data"]) - position * BLOCK_SIZE)


def corrupt_live_blocks(disk, files, rng, count=2):
    """Flip a few bytes inside `count` blocks of different live files (never the first block).

    Live PNGs are preferred; other live files are used only if there are too few PNGs.
    """
    multi_block = [f for f in files if f["status"] == "live" and len(f["blocks"]) >= 2]
    pngs = [f for f in multi_block if f["type"] == "png"]
    others = [f for f in multi_block if f["type"] != "png"]
    choices = [pngs[int(i)] for i in rng.permutation(len(pngs))] + [others[int(i)] for i in rng.permutation(len(others))]
    corrupted = []
    for file in choices[:count]:
        position = int(rng.integers(1, len(file["blocks"])))
        block = file["blocks"][position]
        chunk = bytearray(disk.get(block))
        for offset in rng.choice(file_bytes_in_block(file, position), size=int(rng.integers(1, 4)), replace=False):
            chunk[int(offset)] ^= int(rng.integers(1, 256))
        disk.put(block, bytes(chunk))
        file["corrupted_blocks"].append(block)
        corrupted.append(block)
    return sorted(corrupted)


def duplicate_blocks(disk, files, rng, count=3):
    """Copy undamaged live PNG (or DOCX) blocks (not anchors) into nearby never-used free blocks."""
    sources = [block for f in files if f["status"] == "live" and f["type"] in ("png", "docx")
               for block in f["blocks"][1:] if block not in f["corrupted_blocks"]]
    duplicates = []
    for source in rng.permutation(sources)[:count]:
        source = int(source)
        nearby = [b for b in range(max(0, source - WINDOW), min(NUM_BLOCKS, source + WINDOW + 1))
                  if disk.free[b] and disk.owner[b] is None]
        copy = nearby[int(rng.integers(len(nearby)))]
        disk.put(copy, disk.get(source))
        disk.free[copy] = False  # the copy is now in use, so nothing else lands on it
        duplicates.append({"source": source, "copy": copy})
    return duplicates


# ------------------------------------------------------------------ pipeline

def block_count(file):
    return math.ceil(len(file["data"]) / BLOCK_SIZE)


def simulate(rng, files, pool):
    disk = Disk(rng, pool)
    order = [int(i) for i in rng.permutation(len(files))]
    first_half, second_half = order[:(len(order) + 1) // 2], order[(len(order) + 1) // 2:]
    for index in first_half:                       # 1. write ~half the files sequentially
        gap = int(rng.integers(0, 4))              #    with free blocks left by earlier use
        disk.write_file(index, files[index], disk.sequential(block_count(files[index]), gap))
    doomed = rng.choice(first_half, size=round(len(first_half) / 3), replace=False)
    for index in doomed:                           # 2. delete ~one third
        disk.delete_file(files[int(index)])
    for index in second_half:                      # 3. first-fit into free blocks
        disk.write_file(index, files[index], disk.first_fit(block_count(files[index])))
    for file in files:
        file["corrupted_blocks"] = []
    mark_reused_blocks(disk, files)
    live = [i for i, f in enumerate(files) if f["status"] == "live"]
    for index in rng.choice(live, size=2, replace=False):  # 3b. recent deletions, not yet reused
        disk.delete_file(files[int(index)])
    partially_overwrite_deleted(disk, files, rng)  # 4. partially overwrite 1 deleted file
    corrupted = corrupt_live_blocks(disk, files, rng)   # 5. flip bytes in 2 live blocks
    duplicates = duplicate_blocks(disk, files, rng)     # 6. copy 3 blocks (duplicates)
    return disk, corrupted, duplicates


def self_test(disk, files, duplicates):
    """Rebuild every intact file from its truth block list and compare SHA-256."""
    intact = [f for f in files if not f["corrupted_blocks"] and not f["overwritten_blocks"]]
    for file in intact:
        rebuilt = b"".join(disk.get(block) for block in file["blocks"])[:len(file["data"])]
        if hashlib.sha256(rebuilt).hexdigest() != file["sha256"]:
            raise RuntimeError(f"self-test failed: {file['name']} does not rebuild")
    for pair in duplicates:
        if disk.get(pair["source"]) != disk.get(pair["copy"]):
            raise RuntimeError(f"self-test failed: duplicate {pair} differs from its source")
    return len(intact)


def main():
    parser = argparse.ArgumentParser(description="Generate a synthetic damaged storage image.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--corpus", required=True, choices=sorted(CORPUS_IDS))
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    rng = np.random.default_rng([CORPUS_IDS[args.corpus], args.seed])
    files = build_corpus(rng, args.corpus)
    for file in files:
        file["sha256"] = hashlib.sha256(file["data"]).hexdigest()
    pool = build_residue_pool(rng, args.corpus)
    disk, corrupted, duplicates = simulate(rng, files, pool)

    image_path = os.path.join(ROOT, "data", "cases", f"{args.case}.img")
    truth_path = os.path.join(ROOT, "data", "truth", f"{args.case}.json")
    with open(image_path, "wb") as handle:
        handle.write(disk.data)
    truth = {
        "case_id": args.case, "corpus": args.corpus, "seed": args.seed,
        "block_size": BLOCK_SIZE, "num_blocks": NUM_BLOCKS,
        "image_sha256": hashlib.sha256(disk.data).hexdigest(),
        "files": [{"name": f["name"], "type": f["type"], "size": len(f["data"]), "sha256": f["sha256"],
                   "blocks": f["blocks"], "status": f["status"],
                   "corrupted_blocks": f["corrupted_blocks"], "overwritten_blocks": f["overwritten_blocks"]}
                  for f in files],
        "corrupted_blocks": corrupted,
        "duplicated_blocks": duplicates,
    }
    with open(truth_path, "w") as handle:
        json.dump(truth, handle, indent=2)

    intact_count = self_test(disk, files, duplicates)
    fragmented = [f for f in files if len(runs(f["blocks"])) > 1]
    print(f"case {args.case}  corpus={args.corpus}  seed={args.seed}")
    for file in files:
        layout = " ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in runs(file["blocks"]))
        print(f"  {file['name']:<22} {file['status']:<22} blocks {layout}")
    print(f"files written:     {len(files)}")
    print(f"fragmented:        {len(fragmented)} ({sum(f['type'] == 'png' for f in fragmented)} PNG)")
    print(f"corrupted blocks:  {len(corrupted)} {corrupted}")
    print(f"duplicate blocks:  {len(duplicates)} {[(d['source'], d['copy']) for d in duplicates]}")
    print(f"self-test:         {intact_count}/{intact_count} intact files rebuilt, SHA-256 match")
    print(f"wrote {os.path.relpath(image_path, ROOT)} and {os.path.relpath(truth_path, ROOT)}")


if __name__ == "__main__":
    main()
