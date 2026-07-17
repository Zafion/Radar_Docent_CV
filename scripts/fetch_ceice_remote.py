from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import time
from urllib.parse import urlparse

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.services.discovery.registry import get_discovery_adapters


DEFAULT_OUTPUT_DIR = Path(
    os.environ.get("FUNK_FETCH_OUT_DIR", "/opt/funkcionario-fetcher/out")
)

HEADERS = {
    "User-Agent": "RadarDocentCV-Fetcher/0.1 (+https://funkcionario.com)"
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[-\s]+", "-", value, flags=re.UNICODE)
    return value.strip("-") or "documento"


def filename_from_url(url: str) -> str:
    path = Path(re.sub(r"/+$", "", url.split("?", 1)[0].split("#", 1)[0]))
    return path.name or "documento.pdf"


def build_versioned_filename(original_filename: str, sha256: str) -> str:
    path = Path(original_filename)
    stem = slugify(path.stem or "documento")
    suffix = path.suffix.lower() or ".pdf"
    return f"{stem}__{sha256[:12]}{suffix}"


def hardlink_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def is_safe_relative_path(path_value: str) -> bool:
    path = Path(path_value)
    return not path.is_absolute() and ".." not in path.parts


def download_asset(
    *,
    client: httpx.Client,
    url: str,
    canonical_url: str,
    cache_files_dir: Path,
) -> dict:
    response = client.get(url)
    response.raise_for_status()

    content = response.content
    sha256 = hashlib.sha256(content).hexdigest()
    original_filename = filename_from_url(canonical_url)
    stored_filename = build_versioned_filename(original_filename, sha256)
    cache_file_path = cache_files_dir / stored_filename

    if not cache_file_path.exists():
        cache_file_path.write_bytes(content)

    return {
        "sha256": sha256,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "cache_file_path": str(cache_file_path),
        "content_type": response.headers.get("content-type"),
        "size_bytes": len(content),
        "downloaded_at": utc_now_iso(),
    }


def select_adapters(source_keys: list[str] | None):
    adapters = get_discovery_adapters()

    if not source_keys:
        return adapters

    wanted = set(source_keys)
    selected = [adapter for adapter in adapters if adapter.source_key in wanted]
    found = {adapter.source_key for adapter in selected}
    missing = sorted(wanted - found)

    if missing:
        available = ", ".join(adapter.source_key for adapter in adapters)
        raise SystemExit(
            "Source key no encontrada: "
            + ", ".join(missing)
            + "\nDisponibles: "
            + available
        )

    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch CEICE assets from a local network and build a manifest for Hetzner import."
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directorio base de salida. Default: /opt/funkcionario-fetcher/out",
    )
    parser.add_argument(
        "--source-key",
        action="append",
        default=None,
        help="Filtra por source_key. Se puede repetir. Si se omite, usa todos los adaptadores.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Timeout HTTP en segundos.",
    )
    parser.add_argument(
        "--download-delay",
        type=float,
        default=0.25,
        help="Pausa entre descargas de PDFs.",
    )
    parser.add_argument(
        "--redownload-known",
        action="store_true",
        help="Redescarga URLs ya conocidas en la cache local.",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "cache"
    cache_files_dir = cache_dir / "files"
    current_dir = output_dir / "current"
    current_files_dir = current_dir / "files"
    state_path = output_dir / "state.json"

    cache_files_dir.mkdir(parents=True, exist_ok=True)

    if current_dir.exists():
        shutil.rmtree(current_dir)
    current_files_dir.mkdir(parents=True, exist_ok=True)

    state = read_json(
        state_path,
        {
            "schema_version": 1,
            "assets_by_canonical_url": {},
        },
    )
    assets_by_canonical_url: dict[str, dict] = state.setdefault(
        "assets_by_canonical_url",
        {},
    )

    adapters = select_adapters(args.source_key)

    manifest = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "fetcher_host": platform.node(),
        "project": "Funkcionario / Radar Docent CV",
        "sources": [],
    }

    client = httpx.Client(
        headers=HEADERS,
        timeout=args.timeout,
        follow_redirects=True,
    )

    has_source_failures = False

    try:
        for adapter in adapters:
            started_at = utc_now_iso()
            print("=" * 100)
            print(f"Source: {adapter.source_key}")
            print(f"URL:    {adapter.source_url}")

            source_summary = {
                "source_key": adapter.source_key,
                "source_url": adapter.source_url,
                "source_label": adapter.source_label or adapter.source_key,
                "started_at": started_at,
                "finished_at": None,
                "status": "running",
                "assets": [],
                "results": [],
                "counts": {},
                "error_message": None,
            }

            processed_urls: dict[str, dict] = {}

            try:
                assets = adapter.discover_assets()
                source_summary["assets"] = [asdict(asset) for asset in assets]

                for asset in assets:
                    asset_dict = asdict(asset)

                    if not asset.downloadable:
                        result = {
                            "asset": asset_dict,
                            "status": "non_downloadable",
                            "sha256": None,
                            "original_filename": None,
                            "stored_filename": None,
                            "relative_file_path": None,
                            "content_type": None,
                            "size_bytes": None,
                            "downloaded_at": None,
                            "error_message": None,
                        }
                        source_summary["results"].append(result)
                        continue

                    canonical_url = asset.canonical_url

                    if canonical_url in processed_urls:
                        previous = processed_urls[canonical_url]
                        result = {
                            **previous,
                            "asset": asset_dict,
                            "status": "same_run_duplicate",
                        }
                        source_summary["results"].append(result)
                        continue

                    cached = assets_by_canonical_url.get(canonical_url)
                    cached_file_ok = False

                    if cached and not args.redownload_known:
                        stored_filename = cached.get("stored_filename")
                        if stored_filename:
                            cached_file_path = cache_files_dir / stored_filename
                            cached_file_ok = cached_file_path.exists()

                    if cached and cached_file_ok and not args.redownload_known:
                        stored_filename = cached["stored_filename"]
                        cache_file_path = cache_files_dir / stored_filename
                        current_file_path = current_files_dir / stored_filename
                        hardlink_or_copy(cache_file_path, current_file_path)

                        result = {
                            "asset": asset_dict,
                            "status": "cached_url",
                            "sha256": cached["sha256"],
                            "original_filename": cached["original_filename"],
                            "stored_filename": stored_filename,
                            "relative_file_path": f"files/{stored_filename}",
                            "content_type": cached.get("content_type"),
                            "size_bytes": cached.get("size_bytes"),
                            "downloaded_at": cached.get("downloaded_at"),
                            "error_message": None,
                        }
                    else:
                        try:
                            downloaded = download_asset(
                                client=client,
                                url=asset.url,
                                canonical_url=canonical_url,
                                cache_files_dir=cache_files_dir,
                            )

                            stored_filename = downloaded["stored_filename"]
                            cache_file_path = Path(downloaded["cache_file_path"])
                            current_file_path = current_files_dir / stored_filename
                            hardlink_or_copy(cache_file_path, current_file_path)

                            assets_by_canonical_url[canonical_url] = {
                                "sha256": downloaded["sha256"],
                                "original_filename": downloaded["original_filename"],
                                "stored_filename": stored_filename,
                                "content_type": downloaded["content_type"],
                                "size_bytes": downloaded["size_bytes"],
                                "downloaded_at": downloaded["downloaded_at"],
                            }

                            result = {
                                "asset": asset_dict,
                                "status": "downloaded",
                                "sha256": downloaded["sha256"],
                                "original_filename": downloaded["original_filename"],
                                "stored_filename": stored_filename,
                                "relative_file_path": f"files/{stored_filename}",
                                "content_type": downloaded["content_type"],
                                "size_bytes": downloaded["size_bytes"],
                                "downloaded_at": downloaded["downloaded_at"],
                                "error_message": None,
                            }

                            if args.download_delay > 0:
                                time.sleep(args.download_delay)

                        except Exception as exc:
                            result = {
                                "asset": asset_dict,
                                "status": "download_error",
                                "sha256": None,
                                "original_filename": filename_from_url(canonical_url),
                                "stored_filename": None,
                                "relative_file_path": None,
                                "content_type": None,
                                "size_bytes": None,
                                "downloaded_at": None,
                                "error_message": str(exc),
                            }

                    processed_urls[canonical_url] = result
                    source_summary["results"].append(result)

                finished_at = utc_now_iso()
                results = source_summary["results"]

                source_summary["finished_at"] = finished_at
                source_summary["status"] = "success"
                source_summary["counts"] = {
                    "discovered_assets_count": len(assets),
                    "downloadable_assets_count": sum(1 for item in assets if item.downloadable),
                    "downloaded_count": sum(1 for item in results if item["status"] == "downloaded"),
                    "cached_url_count": sum(1 for item in results if item["status"] == "cached_url"),
                    "same_run_duplicate_count": sum(1 for item in results if item["status"] == "same_run_duplicate"),
                    "non_downloadable_count": sum(1 for item in results if item["status"] == "non_downloadable"),
                    "download_error_count": sum(1 for item in results if item["status"] == "download_error"),
                }

                print(f"Assets descubiertos: {source_summary['counts']['discovered_assets_count']}")
                print(f"Descargables:         {source_summary['counts']['downloadable_assets_count']}")
                print(f"Descargados:          {source_summary['counts']['downloaded_count']}")
                print(f"Desde cache local:    {source_summary['counts']['cached_url_count']}")
                print(f"Errores descarga:     {source_summary['counts']['download_error_count']}")

            except Exception as exc:
                has_source_failures = True
                source_summary["finished_at"] = utc_now_iso()
                source_summary["status"] = "failed"
                source_summary["error_message"] = str(exc)
                source_summary["counts"] = {
                    "discovered_assets_count": len(source_summary["assets"]),
                    "downloadable_assets_count": 0,
                    "downloaded_count": 0,
                    "cached_url_count": 0,
                    "same_run_duplicate_count": 0,
                    "non_downloadable_count": 0,
                    "download_error_count": 0,
                }
                print(f"ERROR: {exc}")

            manifest["sources"].append(source_summary)

    finally:
        client.close()

    write_json(current_dir / "manifest.json", manifest)
    write_json(state_path, state)

    print("=" * 100)
    print(f"Manifest: {current_dir / 'manifest.json'}")
    print(f"Files:    {current_files_dir}")
    print(f"State:    {state_path}")

    return 1 if has_source_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
