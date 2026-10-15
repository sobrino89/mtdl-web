"""Build the film list fixtures in test/fixtures from a real film list. Selection: test/fixtures/README.md.

    python tools/fixture_filmliste.py <Filmliste-akt[.xz]> [<Filmliste-diff[.xz]> ...] <output dir>

Entries keep the order of the original. Channel and topic are re-encoded the way the server does it (set when they
change against the previous entry of the extract, empty otherwise); all other fields are unchanged.
"""

import json
import sys
from collections.abc import Iterable
from pathlib import Path

from mtdl_web.filmlist.parser import (
    CHANNEL,
    DURATION,
    GEO,
    SIZE_MB,
    TIME,
    TIMESTAMP,
    TITLE,
    TOPIC,
    URL,
    URL_HD,
    URL_LOW,
    URL_RTMP,
    URL_SUBTITLE,
    WEBSITE,
    clean_url,
    open_filmlist,
)

HEAD = 25
IN_DIFF = 5
CHANGED_IN_DIFF = 5
NEW_IN_DIFF = 10


def identity(row: list[str]) -> tuple[str, str, str, str]:
    return row[CHANNEL], row[TOPIC], row[URL], row[WEBSITE]


def features(row: list[str]) -> list[str]:
    """Format peculiarities; the extract gets the first entry for each."""
    title, sub, url = row[TITLE], row[URL_SUBTITLE], row[URL]
    checks = {
        "time_hhmm": len(row[TIME]) == 5,
        "time_empty": row[TIME] == "" and row[TIMESTAMP] != "",
        "date_empty": row[TIMESTAMP] == "",
        "duration_empty": row[DURATION] == "",
        "size_empty": row[SIZE_MB] == "",
        "hls": url.split("?")[0].endswith(".m3u8"),
        "subtitle_vtt": sub.endswith(".vtt"),
        "subtitle_ttml": sub.endswith(".ttml"),
        "subtitle_ard_ebutt": "/ebutt/" in sub,
        "subtitle_protocol_relative": sub.startswith("//"),
        "subtitle_html": sub.endswith(".html"),
        "subtitle_in_rtmp_column": row[URL_RTMP] != "",
        "subtitle_double_scheme": sub.count("://") > 1,
        "subtitle_schemeless": sub != "" and "//" not in sub and not sub.startswith("/"),
        "subtitle_path_only": sub.startswith("/") and not sub.startswith("//"),
        "url_broken": clean_url(url) is None,
        "relative_offset_one_digit": row[URL_LOW][:2].endswith("|") or row[URL_HD][:2].endswith("|"),
        "low_empty": row[URL_LOW] == "",
        "hd_empty": row[URL_HD] == "",
        "title_no_break_space": "\N{NO-BREAK SPACE}" in title,
        "title_newline": "\n" in title,
        "title_outer_space": title != title.strip(),
        "date_before_1970": row[TIMESTAMP].startswith("-"),
        "date_far_future": row[TIMESTAMP] != "" and int(row[TIMESTAMP]) > 1_800_000_000,
        "channel_" + row[CHANNEL]: row[CHANNEL] in ("rbtv", "RBTV"),
        "geo_" + row[GEO]: row[GEO] != "",
    }
    return [name for name, hit in checks.items() if hit]


def write(path: Path, meta: tuple[list[str], list[str]], rows: Iterable[list[str]]) -> int:
    count = 0
    prev_channel = prev_topic = None
    with path.open("w", encoding="utf-8") as out:
        out.write("{" + ",\n".join('"Filmliste":' + json.dumps(m, ensure_ascii=False) for m in meta))
        for row in rows:
            encoded = list(row)
            encoded[CHANNEL] = "" if row[CHANNEL] == prev_channel else row[CHANNEL]
            encoded[TOPIC] = "" if row[CHANNEL] == prev_channel and row[TOPIC] == prev_topic else row[TOPIC]
            prev_channel, prev_topic = row[CHANNEL], row[TOPIC]
            out.write(',\n"X":' + json.dumps(encoded, ensure_ascii=False))
            count += 1
        out.write("}\n")
    return count


def key(row: list[str]) -> tuple[str, str, str, str]:
    return row[CHANNEL], row[TOPIC], row[TITLE], row[URL]


def is_candidate(row: list[str]) -> bool:
    text = (row[TOPIC] + " " + row[TITLE]).casefold()
    return "gipfeltreffen" in text or "werwölfe" in text


def main(full_path: Path, diff_paths: list[Path], out_dir: Path) -> None:
    diffs = []
    for path in diff_paths:
        with open_filmlist(path) as diff:
            diffs.append((diff.meta, diff.header, list(diff.rows())))
    first_diff_ids = {identity(r) for r in diffs[0][2]} if diffs else set()
    latest_diff_row = {key(r): r for _, _, rows in diffs for r in rows}

    picked: list[list[str]] = []
    seen: set[str] = set()
    in_diff = changed = 0
    with open_filmlist(full_path) as full:
        header, created = full.header, full.meta.created
        for n, row in enumerate(full.rows()):
            gipfeltreffen = "gipfeltreffen" in (row[TOPIC] + " " + row[TITLE]).casefold()
            take = is_candidate(row) or n < HEAD
            if not gipfeltreffen:  # those must stay exactly the 121 search candidates
                new = [f for f in features(row) if f not in seen]
                seen.update(new)
                take = take or bool(new)
            if not take and identity(row) in first_diff_ids and in_diff < IN_DIFF:
                take = True
                in_diff += 1
            later = latest_diff_row.get(key(row))
            if not take and later is not None and later != row and changed < CHANGED_IN_DIFF:
                take = True  # a later diff changes this entry
                changed += 1
            if take:
                picked.append(row)

    n_full = write(out_dir / f"filmliste-auszug-{created:%Y-%m-%d}.json", header, picked)
    print(f"extract: {n_full} entries, features: {sorted(seen)}")

    picked_ids = {identity(r) for r in picked}
    for meta, diff_header, rows in diffs:
        outside = [r for r in rows if identity(r) not in picked_ids]
        keep = {id(r) for r in outside[:NEW_IN_DIFF]} | {id(r) for r in outside if is_candidate(r)}
        rows_out = [r for r in rows if identity(r) in picked_ids or id(r) in keep]
        name = f"filmliste-diff-auszug-{meta.created:%Y-%m-%d-%H%M}.json"
        print(f"{name}: {write(out_dir / name, diff_header, rows_out)} entries")


if __name__ == "__main__":
    *lists, out = (Path(a) for a in sys.argv[1:])
    main(lists[0], lists[1:], out)
