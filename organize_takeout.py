#!/usr/bin/env python3
"""
Google Photos Takeout Organizer & Metadata Fixer
================================================
A fast, lossless, and robust tool to organize Google Photos Takeout archives.

Key Features:
- 100% Read-Only on source directory (no files deleted, renamed, or modified).
- Re-embeds EXIF metadata (Capture Date & GPS coordinates) directly into file headers.
- Accurate Local Timezone handling (respects Daylight Saving Time / Summer Time).
- Corrects folder classification for midnight photos (00:00 - 00:59 local time).
- Detects and fixes mislabeled HEIC files (JPEGs saved with a .heic extension).
- Injects -QuickTime:AllDates and -api QuickTimeUTC=1 for video containers (MP4, MOV).
- High performance via persistent ExifTool process (-stay_open True) with UTF-8 support.
- Filters out invalid (0.0, 0.0) ocean coordinates.
- Matches modern .supplemental-metadata.json, bracketed duplicates (1), and truncated sidecars.
- Optional exclusion of folders (e.g. Trash, Lixo, Bin).
"""

import os
import sys
import json
import time
import shutil
import re
import argparse
import subprocess
from datetime import datetime

# Ensure unbuffered console output
sys.stdout.reconfigure(line_buffering=True)

IMAGE_EXTS = {
    '.jpg', '.jpeg', '.heic', '.png', '.webp', '.gif',
    '.bmp', '.tiff', '.tif', '.dng', '.cr2', '.nef', '.arw'
}
VIDEO_EXTS = {
    '.mp4', '.mov', '.m4v', '.3gp', '.avi', '.mkv', '.mts', '.wmv'
}
IGNORED_SYSTEM_FILES = {
    'desktop.ini', 'thumbs.db', '.ds_store'
}


class ExifToolSession:
    """Manages a persistent ExifTool background process via -stay_open."""

    def __init__(self, exiftool_path: str):
        self.proc = subprocess.Popen(
            [
                exiftool_path,
                "-stay_open", "True",
                "-charset", "filename=UTF8",
                "-@", "-"
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8"
        )

    def execute(self, commands: list[str]) -> list[str]:
        """Sends a command batch to ExifTool and waits for completion."""
        if not commands:
            return []
        payload = "\n".join(commands) + "\n-execute\n"
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

        output_lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line or line.strip() == "{ready}":
                break
            output_lines.append(line.strip())
        return output_lines

    def close(self):
        """Terminates the ExifTool background process cleanly."""
        try:
            self.proc.stdin.write("-stay_open\nFalse\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def get_unique_destination_path(target_path: str) -> str:
    """Appends an incremental suffix (_1, _2) if target filename already exists."""
    if not os.path.exists(target_path):
        return target_path
    base_dir, filename = os.path.split(target_path)
    root, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = os.path.join(base_dir, f"{root}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def is_jpeg_content(file_path: str) -> bool:
    """Checks whether the file header begins with JPEG magic bytes (JFIF/Exif)."""
    try:
        with open(file_path, "rb") as fp:
            return fp.read(3) == b"\xff\xd8\xff"
    except Exception:
        return False


def find_json_companion(media_path: str, files_lookup: dict[str, str]) -> str | None:
    """
    Finds the matching Takeout JSON metadata sidecar for a media file.
    Handles:
    - Standard .json
    - Modern .supplemental-metadata.json
    - Swapped duplicate brackets: photo(1).jpg -> photo.jpg.supplemental-metadata(1).json
    - Long filename truncations by Google Takeout
    """
    dir_name = os.path.dirname(media_path)
    base_name = os.path.basename(media_path)
    root, ext = os.path.splitext(base_name)

    # 1. Exact: <name>.supplemental-metadata.json
    c1 = f"{base_name}.supplemental-metadata.json".lower()
    if c1 in files_lookup:
        return os.path.join(dir_name, files_lookup[c1])

    # 2. Exact: <name>.json
    c2 = f"{base_name}.json".lower()
    if c2 in files_lookup:
        return os.path.join(dir_name, files_lookup[c2])

    # 3. Stripped extension: <root>.json
    c3 = f"{root}.json".lower()
    if c3 in files_lookup:
        return os.path.join(dir_name, files_lookup[c3])

    # 4. Swapped duplicate brackets e.g. IMG(1).jpg
    m = re.search(r"\(\d+\)$", root)
    if m:
        bracket = m.group(0)
        clean_root = root[:-len(bracket)]

        c4a = f"{clean_root}{ext}.supplemental-metadata{bracket}.json".lower()
        if c4a in files_lookup:
            return os.path.join(dir_name, files_lookup[c4a])

        c4b = f"{clean_root}{ext}{bracket}.json".lower()
        if c4b in files_lookup:
            return os.path.join(dir_name, files_lookup[c4b])

        # Truncated supplemental with bracket suffix
        orig_prefix = f"{clean_root}{ext}.".lower()
        bracket_suffix = f"{bracket}.json".lower()
        for f_lower, f_orig in files_lookup.items():
            if f_lower.startswith(orig_prefix) and f_lower.endswith(bracket_suffix):
                return os.path.join(dir_name, f_orig)

    # 5. Long names / truncated supplemental keyword
    b_lower = base_name.lower()
    for f_lower, f_orig in files_lookup.items():
        if f_lower.endswith(".json") and f_lower.startswith(b_lower) and "supplemental" in f_lower:
            return os.path.join(dir_name, f_orig)

    # 6. Truncated prefix (Google 40-51 character limit)
    if len(base_name) > 40:
        prefix = base_name[:40].lower()
        for f_lower, f_orig in files_lookup.items():
            if f_lower.endswith(".json") and f_lower.startswith(prefix):
                return os.path.join(dir_name, f_orig)

    return None


def extract_metadata(json_path: str | None) -> tuple[int | None, float, float, float, bool]:
    """
    Extracts epoch timestamp and GPS coordinates from a Google Takeout JSON sidecar.
    Returns: (timestamp, latitude, longitude, altitude, has_valid_gps)
    """
    if not json_path or not os.path.exists(json_path):
        return None, 0.0, 0.0, 0.0, False

    try:
        with open(json_path, "r", encoding="utf-8", errors="ignore") as fp:
            data = json.load(fp)
    except Exception:
        return None, 0.0, 0.0, 0.0, False

    # Timestamp extraction (photoTakenTime with creationTime fallback)
    ts = None
    if "photoTakenTime" in data and isinstance(data["photoTakenTime"], dict):
        try:
            ts = int(data["photoTakenTime"].get("timestamp"))
        except (ValueError, TypeError):
            pass
    if not ts and "creationTime" in data and isinstance(data["creationTime"], dict):
        try:
            ts = int(data["creationTime"].get("timestamp"))
        except (ValueError, TypeError):
            pass

    # GPS coordinates: geoData prioritized, fallback to geoDataExif
    lat, lon, alt = 0.0, 0.0, 0.0
    for key in ("geoData", "geoDataExif"):
        if key in data and isinstance(data[key], dict):
            try:
                c_lat = float(data[key].get("latitude", 0.0))
                c_lon = float(data[key].get("longitude", 0.0))
                c_alt = float(data[key].get("altitude", 0.0))
                if c_lat != 0.0 or c_lon != 0.0:
                    lat, lon, alt = c_lat, c_lon, c_alt
                    break
            except (ValueError, TypeError):
                continue

    has_valid_gps = (lat != 0.0 or lon != 0.0)
    return ts, lat, lon, alt, has_valid_gps


def process_takeout(input_dir: str, output_dir: str, exiftool_path: str, exclude_folders: list[str]):
    """Main workflow to organize photos and inject metadata."""
    print("=" * 65, flush=True)
    print("GOOGLE PHOTOS TAKEOUT ORGANIZER & EXIF/GPS INJECTOR", flush=True)
    print("=" * 65, flush=True)
    start_time = time.time()

    if not os.path.exists(input_dir):
        print(f"Error: Input directory does not exist: {input_dir}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    exclude_set = {f.lower().strip() for f in exclude_folders if f.strip()}
    if exclude_set:
        print(f"Excluding directories matching: {', '.join(sorted(exclude_set))}", flush=True)

    print("\n[1/3] Scanning input folders and mapping metadata sidecars...", flush=True)
    media_tasks = []
    allowed_extensions = IMAGE_EXTS | VIDEO_EXTS

    # Traverse all directories in input_dir
    for root_dir, dirs, files in os.walk(input_dir):
        # Prune excluded directories in-place
        dirs[:] = [
            d for d in dirs
            if d.lower() not in exclude_set and not d.startswith(".")
        ]

        files_lookup = {f.lower(): f for f in files}
        media_files = [
            f for f in files
            if os.path.splitext(f)[1].lower() in allowed_extensions
            and f.lower() not in IGNORED_SYSTEM_FILES
            and not f.startswith(".")
        ]

        for mf in media_files:
            media_path = os.path.join(root_dir, mf)
            json_path = find_json_companion(media_path, files_lookup)
            media_tasks.append((media_path, json_path))

    total_tasks = len(media_tasks)
    print(f"Total media files discovered: {total_tasks}", flush=True)
    if total_tasks == 0:
        print("No supported media files found to process. Exiting.", flush=True)
        return

    print("\n[2/3] Initializing persistent ExifTool engine (-stay_open True)...", flush=True)
    exif_session = ExifToolSession(exiftool_path)

    stats = {
        "processed": 0,
        "gps_injected": 0,
        "dates_injected": 0,
        "fake_heic_fixed": 0,
        "videos": 0,
        "images": 0
    }

    print("\n[3/3] Copying files, organizing by Year/Month, and injecting EXIF...", flush=True)

    for idx, (src_media, json_path) in enumerate(media_tasks, 1):
        filename = os.path.basename(src_media)
        ext = os.path.splitext(filename)[1].lower()
        root_name = os.path.splitext(filename)[0]

        ts, lat, lon, alt, has_gps = extract_metadata(json_path)

        # Local time: accurately aligns with camera clocks & daylight saving time
        if ts:
            dt = datetime.fromtimestamp(ts)
        else:
            dt = datetime.fromtimestamp(os.path.getmtime(src_media))

        # Year/Month folder based on local date
        dest_folder = os.path.join(output_dir, f"{dt.year:04d}", f"{dt.month:02d}")
        os.makedirs(dest_folder, exist_ok=True)

        # Fix Samsung/Google fake HEIC files that actually contain JPEG headers
        target_ext = ext
        if ext == ".heic" and is_jpeg_content(src_media):
            target_ext = ".jpg"
            stats["fake_heic_fixed"] += 1

        dest_file_path = get_unique_destination_path(
            os.path.join(dest_folder, f"{root_name}{target_ext}")
        )

        # Safe Copy (Source remains strictly READ-ONLY)
        shutil.copy2(src_media, dest_file_path)
        stats["processed"] += 1

        exif_date_str = dt.strftime("%Y:%m:%d %H:%M:%S")

        cmd = [
            "-overwrite_original",
            "-P"
        ]

        if target_ext in VIDEO_EXTS:
            stats["videos"] += 1
            cmd.extend([
                "-api", "QuickTimeUTC=1",
                f"-QuickTime:AllDates={exif_date_str}"
            ])
            if has_gps:
                cmd.append(f"-QuickTime:GPSCoordinates={lat},{lon},{alt}")
                stats["gps_injected"] += 1
        else:
            stats["images"] += 1
            cmd.extend([
                f"-DateTimeOriginal={exif_date_str}",
                f"-SubSecDateTimeOriginal={exif_date_str}"
            ])
            if has_gps:
                cmd.extend([
                    f"-GPSLatitude={lat}",
                    f"-GPSLatitudeRef={lat}",
                    f"-GPSLongitude={lon}",
                    f"-GPSLongitudeRef={lon}",
                    f"-GPSAltitude={alt}",
                    f"-GPSAltitudeRef={alt}"
                ])
                stats["gps_injected"] += 1

        stats["dates_injected"] += 1
        cmd.append(dest_file_path)

        exif_session.execute(cmd)

        # Preserve filesystem timestamp
        if ts:
            try:
                os.utime(dest_file_path, (ts, ts))
            except Exception:
                pass

        # Progress reporting every 500 files or at finish
        if idx % 500 == 0 or idx == total_tasks:
            pct = (idx / total_tasks) * 100
            elapsed = time.time() - start_time
            speed = idx / elapsed if elapsed > 0 else 1.0
            eta_min = ((total_tasks - idx) / speed) / 60
            print(f"Progress: {idx}/{total_tasks} ({pct:.1f}%) | "
                  f"GPS: {stats['gps_injected']} | "
                  f"Speed: {speed:.1f} files/s | "
                  f"ETA: {eta_min:.1f} min", flush=True)

    exif_session.close()
    total_time = time.time() - start_time

    print("\n" + "=" * 65, flush=True)
    print("SUCCESSFULLY COMPLETED!", flush=True)
    print(f"Total time elapsed: {total_time / 60:.2f} minutes ({total_time:.1f} seconds)", flush=True)
    print(f"Total media files organized: {stats['processed']}", flush=True)
    print(f"Photos with valid GPS headers embedded: {stats['gps_injected']}", flush=True)
    print(f"Capture dates embedded into EXIF: {stats['dates_injected']}", flush=True)
    print(f"Mislabeled HEIC files corrected to .jpg: {stats['fake_heic_fixed']}", flush=True)
    print(f"Output directory: {output_dir}", flush=True)
    print("=" * 65, flush=True)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Organize Google Photos Takeout media and inject accurate EXIF/GPS metadata."
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the extracted Google Photos Takeout directory (e.g. /path/to/Takeout/Google Photos)."
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Destination directory for organized media (organized into YYYY/MM subdirectories)."
    )
    parser.add_argument(
        "--exiftool", "-e",
        default=shutil.which("exiftool"),
        help="Path to the exiftool executable (defaults to auto-detection via system PATH)."
    )
    parser.add_argument(
        "--exclude-folders", "-x",
        nargs="*",
        default=["Trash", "Lixo", "Bin"],
        help="Folder names to skip during scanning (default: Trash, Lixo, Bin)."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    # Validate ExifTool binary
    exiftool_bin = args.exiftool
    if not exiftool_bin or not os.path.isfile(exiftool_bin):
        # On Windows, try checking if adding .exe helps
        if sys.platform == "win32" and exiftool_bin and os.path.isfile(f"{exiftool_bin}.exe"):
            exiftool_bin = f"{exiftool_bin}.exe"
        else:
            print("\nError: ExifTool binary not found!", file=sys.stderr)
            print("ExifTool is required to write EXIF/GPS metadata into media headers.", file=sys.stderr)
            print("\nPlease install ExifTool or pass its path via --exiftool /path/to/exiftool:", file=sys.stderr)
            print("  - Windows: winget install OliverBetz.ExifTool (or download from exiftool.org)", file=sys.stderr)
            print("  - macOS: brew install exiftool", file=sys.stderr)
            print("  - Linux (Ubuntu/Debian): sudo apt install libimage-exiftool-perl", file=sys.stderr)
            sys.exit(1)

    process_takeout(
        input_dir=os.path.abspath(args.input),
        output_dir=os.path.abspath(args.output),
        exiftool_path=os.path.abspath(exiftool_bin),
        exclude_folders=args.exclude_folders
    )
