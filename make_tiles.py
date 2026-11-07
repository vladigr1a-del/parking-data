#!/usr/bin/env python3
"""Turn OpenStreetMap parking objects into small map-tile JSON files.

Usage:  python3 make_tiles.py INPUT OUTPUT_DIR [ZOOM]

INPUT is a GeoJSON file or a GeoJSON-sequence file (one feature per line, as
written by `osmium export -f geojsonseq`). Every feature should be an
amenity=parking object. The script keeps only public parking, finds the centre
of each object and writes it into the tile (a square of the map) it falls in.

Output layout:
  OUTPUT_DIR/meta.json            date and counts
  OUTPUT_DIR/index.html           short page with the data licence
  OUTPUT_DIR/tiles/Z/X/Y.json     list of parking items for one tile

One item is a list:
  [lat, lng, type, fee, customersOnly, capacity, maxStayMinutes, name]
  type: 0 other, 1 surface, 2 multi-storey, 3 underground, 4 street
  fee:  0 unknown, 1 paid, 2 free
"""
import datetime
import json
import math
import os
import re
import shutil
import sys

HIDE_ACCESS = {"private", "no", "permit", "delivery", "residents", "agricultural", "forestry"}
HIDE_TYPE = {"carports", "sheds", "garage", "garage_boxes"}
TYPE_CODE = {"surface": 1, "multi-storey": 2, "underground": 3,
             "street_side": 4, "lane": 4, "on_kerb": 4, "half_on_kerb": 4}


def read_features(path):
    """Yield features from a GeoJSON FeatureCollection or a GeoJSON sequence."""
    with open(path, "r", encoding="utf-8") as f:
        first = f.read(1)
        while first and first.isspace():
            first = f.read(1)
        f.seek(0)
        if first == "{":
            # Could be a FeatureCollection (one big object) or a sequence.
            head = f.readline()
            f.seek(0)
            if '"FeatureCollection"' in head or head.strip() in ("{",):
                data = json.load(f)
                for feat in data.get("features", []):
                    yield feat
                return
        for line in f:
            line = line.strip().lstrip("\x1e").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") == "FeatureCollection":
                for feat in obj.get("features", []):
                    yield feat
            elif obj.get("type") == "Feature":
                yield obj


def coords(geom):
    t = geom.get("type")
    c = geom.get("coordinates")
    if not c:
        return
    if t == "Point":
        yield c
    elif t in ("LineString", "MultiPoint"):
        for q in c:
            yield q
    elif t in ("Polygon", "MultiLineString"):
        for ring in c:
            for q in ring:
                yield q
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                for q in ring:
                    yield q
    elif t == "GeometryCollection":
        for g in geom.get("geometries", []):
            for q in coords(g):
                yield q


def max_stay_minutes(value):
    if not value:
        return 0
    m = re.fullmatch(r"\s*(\d+)\s*(hour|hours|minute|minutes)\s*", value)
    if not m:
        return 0
    n = int(m.group(1))
    return n * 60 if m.group(2).startswith("hour") else n


def tile_xy(lat, lng, z):
    n = 2 ** z
    x = int((lng + 180.0) / 360.0 * n)
    s = math.sin(math.radians(lat))
    y = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src, out = sys.argv[1], sys.argv[2]
    zoom = int(sys.argv[3]) if len(sys.argv) > 3 else 11

    tiles = {}
    total = hidden = skipped = 0
    for feat in read_features(src):
        props = feat.get("properties") or {}
        if props.get("amenity") != "parking":
            continue  # e.g. helper nodes that osmium keeps next to a parking area
        geom = feat.get("geometry") or {}
        pts = list(coords(geom))
        if not pts:
            skipped += 1
            continue
        total += 1
        if props.get("access") in HIDE_ACCESS or props.get("parking") in HIDE_TYPE:
            hidden += 1
            continue
        lats = [q[1] for q in pts]
        lngs = [q[0] for q in pts]
        lat = round((min(lats) + max(lats)) / 2, 5)
        lng = round((min(lngs) + max(lngs)) / 2, 5)
        cap = props.get("capacity", "")
        item = [
            lat,
            lng,
            TYPE_CODE.get(props.get("parking"), 0),
            {"yes": 1, "no": 2}.get(props.get("fee"), 0),
            1 if props.get("access") in ("customers", "destination", "visitors") else 0,
            int(cap) if isinstance(cap, str) and cap.isdigit() else 0,
            max_stay_minutes(props.get("maxstay")),
            (props.get("name") or "")[:60],
        ]
        tiles.setdefault(tile_xy(lat, lng, zoom), []).append(item)

    if os.path.isdir(out):
        shutil.rmtree(out)
    for (x, y), items in tiles.items():
        d = os.path.join(out, "tiles", str(zoom), str(x))
        os.makedirs(d, exist_ok=True)
        items.sort()
        with open(os.path.join(d, "%d.json" % y), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, separators=(",", ":"))

    date = os.environ.get("DATA_DATE") or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    meta = {"date": date, "zoom": zoom, "objects": total, "hidden": hidden,
            "shown": total - hidden, "tiles": len(tiles)}
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, separators=(",", ":"))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Parking data</title></head><body>"
                "<h1>Parking data tiles</h1>"
                "<p>Built " + date + ". Contains information from OpenStreetMap, "
                "&copy; OpenStreetMap contributors, available under the "
                "<a href=\"https://opendatacommons.org/licenses/odbl/1-0/\">Open Database Licence (ODbL) 1.0</a>. "
                "See <a href=\"https://www.openstreetmap.org/copyright\">openstreetmap.org/copyright</a>.</p>"
                "<p>Objects read: %d, hidden (private etc.): %d, shown: %d, tiles: %d.</p>"
                "</body></html>" % (total, hidden, total - hidden, len(tiles)))
    print("objects: %d  hidden: %d  shown: %d  tiles: %d  skipped(no geometry): %d" % (total, hidden, total - hidden, len(tiles), skipped))


if __name__ == "__main__":
    main()
