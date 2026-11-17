from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone, timedelta
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
import tempfile

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.services.discovery.registry import get_discovery_adapters
from app.services.document_payload import (
    MAX_DOWNLOAD_BYTES, atomic_write_document, filename_from_url as document_filename,
    read_document_bytes, validate_document,
)


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
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".fetch-json-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)



def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def should_retry_error(error_info: dict, retry_after: timedelta) -> bool:
    last_error_at = parse_iso_datetime(error_info.get("last_error_at"))

    if last_error_at is None:
        return True

    if last_error_at.tzinfo is None:
        last_error_at = last_error_at.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) - last_error_at >= retry_after


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[-\s]+", "-", value, flags=re.UNICODE)
    return value.strip("-") or "documento"


def filename_from_url(url: str) -> str:
    return document_filename(url)



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


def download_asset(
    *,
    client: httpx.Client,
    url: str,
    canonical_url: str,
    cache_files_dir: Path,
    previous: dict | None = None,
    use_conditional_headers: bool = True,
    max_download_bytes: int = MAX_DOWNLOAD_BYTES,
) -> dict:
    cache_valid = False
    if previous and previous.get("stored_filename"):
        try:
            cached_bytes = (cache_files_dir / previous["stored_filename"]).read_bytes()
            if len(cached_bytes) <= max_download_bytes and hashlib.sha256(cached_bytes).hexdigest() == previous.get("sha256"):
                validate_document(cached_bytes, canonical_url)
                cache_valid = True
        except (ValueError, OSError):
            pass
    conditional = {}
    if previous and use_conditional_headers and cache_valid:
        if previous.get("etag"):
            conditional["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            conditional["If-Modified-Since"] = previous["last_modified"]
    with client.stream("GET", url, headers=conditional) as response:
        checked_at = utc_now_iso()
        if response.status_code == 304:
            if not previous or not cache_valid:
                raise ValueError("http_304_without_valid_cached_file")
            return {
                **previous,
                "cache_file_path": str(cache_files_dir / previous["stored_filename"]),
                "last_checked_at": checked_at,
                "etag": response.headers.get("etag") or previous.get("etag"),
                "last_modified": response.headers.get("last-modified") or previous.get("last_modified"),
                "not_modified": True,
            }
        response.raise_for_status()
        payload = validate_document(read_document_bytes(response, max_download_bytes), canonical_url, response.headers)
        response_headers = dict(response.headers)
    content = payload.content
    sha256 = hashlib.sha256(content).hexdigest()
    original_filename = payload.original_filename
    stored_filename = build_versioned_filename(original_filename, sha256)
    cache_file_path = cache_files_dir / stored_filename

    atomic_write_document(cache_file_path, content)

    history = list((previous or {}).get("previous_versions") or [])
    hash_changes = list((previous or {}).get("hash_changes") or [])
    if previous and previous.get("sha256") != sha256:
        hash_changes.append({"checked_at": checked_at, "from_sha256": previous.get("sha256"), "to_sha256": sha256})
        prior = {key: previous.get(key) for key in CACHE_METADATA_KEYS if key not in {"previous_versions", "hash_changes"}}
        prior["replaced_at"] = checked_at
        if not any(item.get("sha256") == prior["sha256"] for item in history):
            history.append(prior)
    return {
        "sha256": sha256,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "cache_file_path": str(cache_file_path),
        "content_type": payload.content_type,
        "size_bytes": len(content),
        "downloaded_at": checked_at,
        "last_checked_at": checked_at,
        "etag": response_headers.get("etag"),
        "last_modified": response_headers.get("last-modified"),
        "previous_versions": history,
        "hash_changes": hash_changes,
        "not_modified": False,
    }


CACHE_METADATA_KEYS = (
    "sha256", "original_filename", "stored_filename", "content_type", "size_bytes",
    "downloaded_at", "last_checked_at", "etag", "last_modified", "previous_versions", "hash_changes",
)


def cache_metadata(downloaded: dict) -> dict:
    return {key: downloaded.get(key) for key in CACHE_METADATA_KEYS}


def revalidation_due(cached: dict, ttl: timedelta) -> bool:
    stamp = parse_iso_datetime(cached.get("last_checked_at") or cached.get("downloaded_at"))
    if stamp is None:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - stamp >= ttl


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
    parser.add_argument(
        "--retry-download-errors-after-hours",
        type=float,
        default=24.0,
        help="Horas antes de reintentar un PDF que falló al descargarse. Default: 24.",
    )

    parser.add_argument("--revalidate-after-hours", type=float, default=24.0,
                        help="TTL de comprobación de URLs conocidas; default: 24 horas.")
    parser.add_argument("--max-revalidations", type=int, default=10,
                        help="Máximo de revalidaciones por ejecución; default: 10. No limita --redownload-known.")
    parser.add_argument("--max-download-mb", type=float, default=50.0, help="Límite por documento, default: 50 MiB.")
    args = parser.parse_args()
    if args.max_download_mb <= 0:
        parser.error("El límite de descarga debe ser positivo")
    max_download_bytes = int(args.max_download_mb * 1024 * 1024)
    if args.revalidate_after_hours < 0 or args.max_revalidations < 0:
        parser.error("TTL y límite de revalidación deben ser no negativos")
    revalidation_ttl = timedelta(hours=args.revalidate_after_hours)
    revalidations_left = args.max_revalidations

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
            "errors_by_canonical_url": {},
        },
    )

    assets_by_canonical_url: dict[str, dict] = state.setdefault(
        "assets_by_canonical_url",
        {},
    )
    errors_by_canonical_url: dict[str, dict] = state.setdefault(
        "errors_by_canonical_url",
        {},
    )
    retry_error_after = timedelta(hours=args.retry_download_errors_after_hours)

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
                diagnostics = getattr(adapter, "discovery_errors", [])
                diagnostics = diagnostics if isinstance(diagnostics, list) else []
                source_summary["discovery_errors"] = [error for error in diagnostics if not error.get("optional_probe")]
                source_summary["optional_probe_misses"] = [error for error in diagnostics if error.get("optional_probe")]
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
                    cached_file_ok = bool(cached and cached.get("stored_filename")
                                          and (cache_files_dir / cached["stored_filename"]).is_file())
                    cached_error = errors_by_canonical_url.get(canonical_url)
                    if (
                        cached_error
                        and not cached_file_ok
                        and not args.redownload_known
                        and not should_retry_error(cached_error, retry_error_after)
                    ):
                        result = {
                            "asset": asset_dict,
                            "status": "download_error_cached",
                            "sha256": None,
                            "original_filename": cached_error.get(
                                "original_filename",
                                filename_from_url(canonical_url),
                            ),
                            "stored_filename": None,
                            "relative_file_path": None,
                            "content_type": None,
                            "size_bytes": None,
                            "downloaded_at": None,
                            "error_message": cached_error.get("error_message"),
                            "last_error_at": cached_error.get("last_error_at"),
                        }
                        processed_urls[canonical_url] = result
                        source_summary["results"].append(result)
                        continue

                    if cached and cached_file_ok and not args.redownload_known:
                        result_status = "cached_url"
                        revalidation_error = None
                        revalidation_status = "fresh"
                        previous_sha256 = cached.get("sha256")
                        if cached_error and not should_retry_error(cached_error, retry_error_after):
                            revalidation_error = cached_error.get("error_message")
                            revalidation_status = "error_backoff"
                        elif revalidation_due(cached, revalidation_ttl):
                            if revalidations_left > 0:
                                revalidations_left -= 1
                                try:
                                    checked = download_asset(client=client, url=asset.url,
                                        canonical_url=canonical_url, cache_files_dir=cache_files_dir, previous=cached, max_download_bytes=max_download_bytes)
                                    cached = {**cached, **cache_metadata(checked)}
                                    assets_by_canonical_url[canonical_url] = cached
                                    errors_by_canonical_url.pop(canonical_url, None)
                                    revalidation_status = "not_modified" if checked.get("not_modified") else "downloaded"
                                    if not checked.get("not_modified"):
                                        result_status = "downloaded"
                                except Exception as exc:
                                    revalidation_error = str(exc)
                                    revalidation_status = "failed"
                                    errors_by_canonical_url[canonical_url] = {
                                        "last_error_at": utc_now_iso(), "error_message": str(exc),
                                        "original_filename": cached.get("original_filename"),
                                        "url": asset.url, "title": asset.title,
                                    }
                                if args.download_delay > 0:
                                    time.sleep(args.download_delay)
                            else:
                                revalidation_status = "budget_deferred"
                        stored_filename = cached["stored_filename"]
                        cache_file_path = cache_files_dir / stored_filename
                        current_file_path = current_files_dir / stored_filename
                        hardlink_or_copy(cache_file_path, current_file_path)

                        result = {
                            "asset": asset_dict,
                            "status": result_status,
                            "revalidation_status": revalidation_status,
                            "revalidation_error": revalidation_error,
                            "last_checked_at": cached.get("last_checked_at"),
                            "previous_sha256": previous_sha256 if previous_sha256 != cached["sha256"] else None,
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
                                previous=cached,
                                use_conditional_headers=False,
                                max_download_bytes=max_download_bytes,
                            )

                            stored_filename = downloaded["stored_filename"]
                            cache_file_path = Path(downloaded["cache_file_path"])
                            current_file_path = current_files_dir / stored_filename
                            hardlink_or_copy(cache_file_path, current_file_path)

                            assets_by_canonical_url[canonical_url] = cache_metadata(downloaded)
                            errors_by_canonical_url.pop(canonical_url, None)

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
                                "last_checked_at": downloaded.get("last_checked_at"),
                                "error_message": None,
                            }

                            if args.download_delay > 0:
                                time.sleep(args.download_delay)

                        except Exception as exc:
                            error_message = str(exc)
                            errors_by_canonical_url[canonical_url] = {
                                "last_error_at": utc_now_iso(),
                                "error_message": error_message,
                                "original_filename": filename_from_url(canonical_url),
                                "title": asset.title,
                                "url": asset.url,
                            }

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
                                "error_message": error_message,
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
                    "download_error_count": sum(
                        1
                        for item in results
                        if item["status"] in {"download_error", "download_error_cached"}
                    ),
                    "revalidation_error_count": sum(bool(item.get("revalidation_error")) for item in results),
                    "revalidated_count": sum(item.get("revalidation_status") in {"not_modified", "downloaded"} for item in results),
                    "revalidation_deferred_count": sum(item.get("revalidation_status") == "budget_deferred" for item in results),
                }

                if source_summary["counts"]["download_error_count"] or any(item.get("revalidation_error") for item in results):
                    source_summary["status"] = "partial"
                    source_summary["error_message"] = "document_download_or_revalidation_errors"
                    has_source_failures = True

                if source_summary.get("discovery_errors"):
                    source_summary["status"] = "partial" if assets else "failed"
                    source_summary["error_message"] = "discovery_errors"
                    source_summary["counts"]["discovery_error_count"] = len(source_summary["discovery_errors"])
                    has_source_failures = True

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
