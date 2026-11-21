# Google Photos Takeout EXIF & GPS Organizer 📸🧭

A high-performance, lossless, and robust Python command-line utility to clean up, chronologically organize, and re-embed EXIF and GPS metadata into media exported from **Google Takeout**.

---

## The Problem

When you download your photo library via **Google Takeout**, Google splits your library into dozens of messy date and album folders, strips critical metadata tags from file headers, and stores the capture timestamps, geolocation coordinates, and descriptions into companion `.json` sidecars (such as `filename.jpg.supplemental-metadata.json`).

Furthermore, moving these raw files or viewing them outside Google Photos causes major headaches:
- Files lose their original creation dates and default to the download timestamp.
- GPS geolocation coordinates are missing from EXIF headers, so photo map features stop working.
- Daylight Saving Time (DST) offsets often cause exported timestamps to be shifted by an hour.
- Photos captured shortly after midnight (00:00 - 00:59 local time) are wrongly classified into the previous month's folder due to UTC timestamps.
- Many `.heic` files exported by Google are actually standard JPEG images with incorrect `.heic` extensions, causing incompatibility with desktop photo viewers.

---

## Key Features

- **100% Read-Only Safety**: The script never deletes, renames, or modifies files in your source Takeout directory. Your original download remains completely untouched.
- **Direct EXIF & QuickTime Header Embedding**: Uses Phil Harvey's industry-standard **ExifTool** to embed `DateTimeOriginal`, `SubSecDateTimeOriginal`, `GPSLatitude`, `GPSLongitude`, and `GPSAltitude` directly into image files (`.jpg`, `.heic`, `.png`, `.webp`, `.dng`, etc.).
- **Accurate Local Timezone & DST**: Uses local system timezone conversion (`datetime.fromtimestamp`) so that winter and summer photo timestamps match the camera's local clock to the exact second.
- **Midnight Transition Correction**: Properly calculates the local capture date for midnight photos so they land in the correct `YYYY/MM` folder rather than the previous month.
- **Fake HEIC Detection & Auto-Correction**: Analyzes the file header magic bytes (`\xff\xd8\xff`) to detect JPEGs masquerading as `.heic` files, saving them cleanly as `.jpg` so they open instantly everywhere.
- **Full Video Support**: Injects `-api QuickTimeUTC=1` and `-QuickTime:AllDates` into video containers (`.mp4`, `.mov`, `.m4v`, `.3gp`), correctly setting `CreateDate`, `TrackCreateDate`, `MediaCreateDate`, and video GPS coordinates.
- **Strict (0.0, 0.0) Ocean Coordinate Filtering**: Filters out unassigned `0.0, 0.0` coordinates (common in screenshots or messaging apps) to avoid falsely locating photos in the Gulf of Guinea. Checks `geoData` first with an automated fallback to `geoDataExif`.
- **Advanced Sidecar Matching**: Seamlessly pairs media with:
  - Modern `*.supplemental-metadata.json` sidecars.
  - Legacy `*.json` sidecars.
  - Bracket-swapped duplicate names: `photo(1).jpg` $\leftrightarrow$ `photo.jpg.supplemental-metadata(1).json`.
  - Long filenames truncated by Google's character limits.
- **Blazing Fast via Persistent ExifTool**: Keeps a single ExifTool instance open in the background via `-stay_open True -charset filename=UTF8`, processing 40+ photos per second (over 10,000 photos in ~4 minutes).
- **Directory Exclusion**: Easily exclude unwanted folders (defaults to `Trash`, `Lixo`, `Bin`).
- **Zero Third-Party Python Dependencies**: Built entirely using the Python standard library.

---

## Prerequisites

1. **Python 3.10+**: Ensure Python is installed on your system.
2. **ExifTool**: Ensure ExifTool is installed on your machine.
   - **Windows**:
     ```powershell
     winget install OliverBetz.ExifTool
     ```
     *(Or download the standalone executable from [exiftool.org](https://exiftool.org/) and add it to your PATH).*
   - **macOS**:
     ```bash
     brew install exiftool
     ```
   - **Linux (Ubuntu/Debian)**:
     ```bash
     sudo apt install libimage-exiftool-perl
     ```

---

## Installation

Clone this repository to your local machine:

```bash
git clone https://github.com/jd164/google-photos-takeout-exif-organizer.git
cd google-photos-takeout-exif-organizer
```

---

## Usage

Extract all your Google Takeout zip files into a single directory so that all folders merge together. Then run:

### Basic Command

```bash
python organize_takeout.py --input "/path/to/Takeout/Google Photos" --output "/path/to/Organized_Photos"
```

### Advanced Options

```bash
# Specifying a custom path to the ExifTool executable
python organize_takeout.py \
  --input "C:\Takeout\Google Photos" \
  --output "C:\Organized_Photos" \
  --exiftool "C:\Tools\exiftool.exe"

# Customizing excluded folders
python organize_takeout.py \
  --input "/path/to/Google Photos" \
  --output "/path/to/Organized" \
  --exclude-folders "Trash" "Bin" "Archive" "Drafts"
```

### CLI Arguments Reference

| Argument | Shorthand | Required | Description |
| :--- | :---: | :---: | :--- |
| `--input` | `-i` | **Yes** | Path to the unzipped Google Photos Takeout root directory. |
| `--output` | `-o` | **Yes** | Destination directory where organized `YYYY/MM` folders will be created. |
| `--exiftool` | `-e` | No | Path to the `exiftool` executable (defaults to automatic discovery via system `PATH`). |
| `--exclude-folders` | `-x` | No | List of folder names to skip (default: `Trash`, `Lixo`, `Bin`). |

---

## Note on Album Folders & Duplicates

Google Photos Takeout often exports photos in both the main year folders (e.g. `Photos from 2024`) **and** in separate user album folders (e.g. `Vacation 2024`), creating duplicate copies across folders.

`organize_takeout.py` safely handles name collisions at the destination by appending an incremental suffix (`_1`, `_2`) so that **no photo is ever overwritten or lost**. If your export includes user albums and you only want one unique copy of each photo, you can either:
1. Deselect user albums in Google Takeout before exporting and export only the date folders; or
2. Run standard deduplication tools (e.g. `czkawka`, `rmlint`, or duplicate finders) on your final output folder.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
