"""Download and extract arXiv source tarballs for the candidate thesis papers.

Idempotent: skips any tarball or unpacked folder that already exists on disk.
Versions are pinned for reproducibility — edit ``PAPERS`` to add or remove.

Run with:  uv run scripts/fetch_arxiv_sources.py
"""

from __future__ import annotations

import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARXIV_DIR = REPO_ROOT / "arxiv-papers"

PAPERS: tuple[str, ...] = (
    "2302.05882v1",
    "2305.18502v2",
    "2402.03220v3",
    "2405.15459v2",
    "2406.02157v1",
    "2506.02651v1",
    "2602.16609v1",
    "2605.13612v1",
)

USER_AGENT = "phd-thesis-fetch/1.0 (luca@arnaboldi.lu)"
ARXIV_SRC_URL = "https://arxiv.org/src/{paper_id}"
SLEEP_BETWEEN_DOWNLOADS = 3.0  # arXiv asks scripts to throttle


def download(paper_id: str, dest: Path) -> None:
    url = ARXIV_SRC_URL.format(paper_id=paper_id)
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as response:
        data = response.read()
    dest.write_bytes(data)


def extract(tarball: Path, folder: Path) -> None:
    print(f"  extracting into {folder.name}/")
    folder.mkdir()
    with tarfile.open(tarball, mode="r:gz") as tar:
        try:
            tar.extractall(folder, filter="data")
        except TypeError:
            # Older Python (<3.10.12 / <3.11.4) without the ``filter`` kwarg.
            tar.extractall(folder)


def fetch_one(paper_id: str) -> bool:
    """Process one paper. Return True if a network download happened."""
    tarball = ARXIV_DIR / f"arXiv-{paper_id}.tar.gz"
    folder = ARXIV_DIR / f"arXiv-{paper_id}"

    downloaded = False
    if tarball.exists():
        print(f"  tarball present: {tarball.name}")
    else:
        download(paper_id, tarball)
        downloaded = True

    if folder.exists():
        print(f"  folder present:  {folder.name}/")
    else:
        extract(tarball, folder)

    return downloaded


def main() -> int:
    ARXIV_DIR.mkdir(parents=True, exist_ok=True)

    for i, paper_id in enumerate(PAPERS):
        print(f"\n=== arXiv:{paper_id} ===")
        try:
            did_download = fetch_one(paper_id)
        except (urllib.error.URLError, OSError, tarfile.TarError) as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            return 1
        if did_download and i < len(PAPERS) - 1:
            time.sleep(SLEEP_BETWEEN_DOWNLOADS)

    print("\nAll papers ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
