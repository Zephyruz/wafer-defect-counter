from __future__ import annotations

from email import policy
from email.parser import BytesParser
import json
import mimetypes
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "web_data"
BATCHES_DIR = DATA_DIR / "batches"
STATIC_DIR = ROOT / "web_static"
CERT_FILE = ROOT / "cert.pem"
KEY_FILE = ROOT / "key.pem"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = 120 * 1024 * 1024
MAX_JSON_REQUEST_BYTES = 2 * 1024 * 1024

executor = ThreadPoolExecutor(max_workers=2)
store_lock = threading.Lock()


@dataclass
class UploadedFile:
    filename: str
    data: bytes


@dataclass
class ParsedForm:
    fields: dict[str, str]
    files: list[UploadedFile]

    def getfirst(self, name: str, default: str | None = None) -> str | None:
        return self.fields.get(name, default)


class UploadLimitError(ValueError):
    pass


class RequestValidationError(ValueError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def ensure_dirs() -> None:
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def batch_dir(batch_id: str) -> Path:
    return BATCHES_DIR / batch_id


def batch_file(batch_id: str) -> Path:
    return batch_dir(batch_id) / "batch.json"


def read_batch(batch_id: str) -> dict:
    path = batch_file(batch_id)
    if not path.exists():
        raise FileNotFoundError(batch_id)
    batch = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for image in batch.get("images", []):
        summary = image.get("summary")
        if isinstance(summary, dict) and "chips" in summary:
            summary.pop("chips", None)
            changed = True
        if image.get("status") == "processing" and image.get("summary"):
            image["status"] = "done"
            changed = True
        if image.get("status") == "processing" and now_ms() - int(image.get("started_at") or 0) > 10 * 60 * 1000:
            image["status"] = "failed"
            image["error"] = "处理超时，请重新点位后再统计"
            changed = True
    if changed:
        write_batch(batch)
    return batch


def write_batch(batch: dict) -> None:
    path = batch_file(batch["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def list_batches() -> list[dict]:
    batches = []
    for path in BATCHES_DIR.glob("*/batch.json"):
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
            batches.append(batch_summary(batch))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(batches, key=lambda item: item["created_at"], reverse=True)


def stop_open_batches() -> None:
    for path in BATCHES_DIR.glob("*/batch.json"):
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not batch.get("stopped"):
            batch["stopped"] = True
            batch["stopped_at"] = now_ms()
            write_batch(batch)


def batch_summary(batch: dict) -> dict:
    images = batch.get("images", [])
    included = [image for image in images if not image.get("excluded")]
    done = [image for image in included if image.get("status") == "done"]
    processing = [image for image in images if image.get("status") == "processing"]
    failed = [image for image in included if image.get("status") == "failed"]
    excluded = [image for image in images if image.get("excluded")]
    total_chips = sum(image.get("summary", {}).get("total_chips", 0) for image in done)
    defective_chips = sum(corrected_defective_chips(image) for image in done)
    good_chips = total_chips - defective_chips
    return {
        "id": batch["id"],
        "name": batch["name"],
        "created_at": batch["created_at"],
        "image_count": len(images),
        "done_count": len(done),
        "processing_count": len(processing),
        "failed_count": len(failed),
        "excluded_count": len(excluded),
        "total_chips": total_chips,
        "good_chips": good_chips,
        "defective_chips": defective_chips,
        "defect_rate": defective_chips / total_chips if total_chips else 0,
        "has_calibration": False,
        "stopped": bool(batch.get("stopped")),
    }


def corrected_defective_chips(image: dict) -> int:
    summary = image.get("summary") or {}
    total = int(summary.get("total_chips") or 0)
    defective = int(summary.get("defective_chips") or 0)
    delta = int(image.get("manual_ng_delta") or 0)
    return max(0, min(total, defective + delta))


def safe_filename(name: str) -> str:
    stem = Path(name).stem or "image"
    suffix = Path(name).suffix.lower() or ".jpg"
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    return f"{cleaned[:80]}{suffix}"


def format_mb(size: int) -> str:
    return f"{size / 1024 / 1024:.0f} MB"


def validate_uploaded_files(files: list[UploadedFile]) -> None:
    if len(files) > MAX_UPLOAD_FILES:
        raise UploadLimitError(f"一次最多上传 {MAX_UPLOAD_FILES} 张图片")
    for file_item in files:
        size = len(file_item.data)
        if size > MAX_UPLOAD_FILE_BYTES:
            raise UploadLimitError(
                f"{file_item.filename} 太大：{format_mb(size)}，单张最多 {format_mb(MAX_UPLOAD_FILE_BYTES)}"
            )


def parse_content_length(headers, max_bytes: int) -> int:
    raw = headers.get("Content-Length", "0")
    try:
        length = int(raw)
    except ValueError as exc:
        raise RequestValidationError("请求长度无效") from exc
    if length < 0:
        raise RequestValidationError("请求长度无效")
    if length > max_bytes:
        raise UploadLimitError(f"一次上传总量最多 {format_mb(max_bytes)}")
    return length


def resolve_inside(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise FileNotFoundError from exc
    if not resolved.is_file():
        raise FileNotFoundError
    return resolved


def media_url(path: str | Path) -> str:
    resolved = Path(path).resolve()
    rel = resolved.relative_to(DATA_DIR.resolve()).as_posix()
    version = int(resolved.stat().st_mtime) if resolved.exists() else now_ms()
    return f"/media/{rel}?v={version}"


def image_for_response(image: dict) -> dict:
    copied = dict(image)
    copied["url"] = media_url(image["path"])
    if copied.get("preview_image"):
        copied["preview_url"] = media_url(copied["preview_image"])
    summary = copied.get("summary")
    if summary:
        summary = dict(summary)
        adjusted_defective = corrected_defective_chips(copied)
        total = int(summary.get("total_chips") or 0)
        summary["raw_defective_chips"] = int(summary.get("defective_chips") or 0)
        summary["manual_ng_delta"] = int(copied.get("manual_ng_delta") or 0)
        summary["defective_chips"] = adjusted_defective
        summary["good_chips"] = total - adjusted_defective
        summary["defect_rate"] = adjusted_defective / total if total else 0
        summary["result_url"] = media_url(summary["result_image"])
        if summary.get("review_image"):
            summary["review_url"] = media_url(summary["review_image"])
        summary["debug_url"] = media_url(summary["debug_image"])
        if summary.get("chip_report"):
            summary["chip_report_url"] = media_url(summary["chip_report"])
        summary.pop("chips", None)
        copied["summary"] = summary
    return copied


def compact_summary(summary: dict | None) -> dict | None:
    if not summary:
        return None
    compacted = dict(summary)
    compacted.pop("chips", None)
    return compacted


def add_uploaded_file(batch: dict, file_item) -> dict | None:
    if not getattr(file_item, "filename", None):
        return None
    suffix = Path(file_item.filename).suffix.lower() or ".jpg"
    if suffix not in ALLOWED_EXTENSIONS:
        return None
    image_id = uuid.uuid4().hex[:10]
    uploads_dir = batch_dir(batch["id"]) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{image_id}_{safe_filename(file_item.filename)}"
    target = uploads_dir / filename
    with target.open("wb") as output:
        output.write(file_item.data)
    image = {
        "id": image_id,
        "filename": file_item.filename,
        "path": str(target),
        "status": "waiting_points",
        "points": None,
        "summary": None,
        "error": None,
        "excluded": False,
        "created_at": now_ms(),
    }
    batch["images"].append(image)
    return image


def queue_images(batch_id: str, image_ids: list[str], points: list[list[float]]) -> None:
    for image_id in image_ids:
        executor.submit(process_image, batch_id, image_id, points)


def process_image(batch_id: str, image_id: str, points: list[list[float]]) -> None:
    with store_lock:
        batch = read_batch(batch_id)
        image = next(item for item in batch["images"] if item["id"] == image_id)
        if image.get("excluded"):
            return
        image["status"] = "processing"
        image["started_at"] = now_ms()
        image["points"] = points
        image["error"] = None
        image["manual_ng_delta"] = 0
        image.pop("corrected_at", None)
        write_batch(batch)

    try:
        from wafer_defect_counter import process_image_file

        output_dir = batch_dir(batch_id) / "results" / image_id
        summary = process_image_file(Path(image["path"]), points, output_dir)
        summary_dict = compact_summary(asdict(summary))
        status = "done"
        error = None
    except Exception as exc:
        summary_dict = None
        status = "failed"
        error = str(exc)

    with store_lock:
        batch = read_batch(batch_id)
        image = next(item for item in batch["images"] if item["id"] == image_id)
        if image.get("excluded"):
            return
        image["status"] = status
        image["summary"] = summary_dict
        image["error"] = error
        image["finished_at"] = now_ms()
        write_batch(batch)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "WaferWeb/0.7"

    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.serve_file(STATIC_DIR / "index.html")
            return
        if path == "/capture":
            self.serve_file(STATIC_DIR / "capture.html")
            return
        if path == "/live-capture":
            self.serve_file(STATIC_DIR / "live_capture.html")
            return
        if path.startswith("/static/"):
            self.serve_file(STATIC_DIR / unquote(path.removeprefix("/static/")))
            return
        if path.startswith("/media/"):
            self.serve_media(path.removeprefix("/media/"))
            return
        if path == "/api/batches":
            self.send_json({"batches": list_batches()})
            return
        if path == "/api/version":
            self.send_json({
                "version": "0.7",
                "grid_preview": True,
                "manual_points_per_image": True,
                "review_image_without_indices": True,
                "manual_ng_correction": True,
            })
            return
        if path.startswith("/api/batches/"):
            batch_id = path.split("/", 3)[3]
            try:
                batch = read_batch(batch_id)
            except FileNotFoundError:
                self.send_error_json("批次不存在", HTTPStatus.NOT_FOUND)
                return
            batch = dict(batch)
            batch["summary"] = batch_summary(batch)
            batch["images"] = [image_for_response(image) for image in batch["images"]]
            self.send_json(batch)
            return
        self.send_error_json("页面不存在", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if parsed.path == "/api/batches":
            self.create_batch()
            return
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "batches" and parts[3] == "stop":
            self.stop_batch(parts[2])
            return
        if len(parts) >= 6 and parts[0] == "api" and parts[1] == "batches" and parts[3] == "images" and parts[5] == "exclude":
            self.exclude_image(parts[2], parts[4])
            return
        if len(parts) >= 6 and parts[0] == "api" and parts[1] == "batches" and parts[3] == "images" and parts[5] == "preview":
            self.preview_grid(parts[2], parts[4])
            return
        if len(parts) >= 6 and parts[0] == "api" and parts[1] == "batches" and parts[3] == "images" and parts[5] == "correction":
            self.correct_image(parts[2], parts[4])
            return
        if len(parts) >= 6 and parts[0] == "api" and parts[1] == "batches" and parts[3] == "images" and parts[5] == "reset-correction":
            self.reset_image_correction(parts[2], parts[4])
            return
        if parsed.path.startswith("/api/batches/") and parsed.path.endswith("/images"):
            batch_id = parsed.path.split("/")[3]
            self.add_images(batch_id)
            return
        if parsed.path.startswith("/api/batches/") and parsed.path.endswith("/points"):
            batch_id = parsed.path.split("/")[3]
            self.submit_points(batch_id)
            return
        self.send_error_json("接口不存在", HTTPStatus.NOT_FOUND)

    def read_json_body(self) -> dict:
        length = parse_content_length(self.headers, MAX_JSON_REQUEST_BYTES)
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def read_form(self) -> ParsedForm:
        length = parse_content_length(self.headers, MAX_UPLOAD_REQUEST_BYTES)
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise RequestValidationError("请求格式必须是 multipart/form-data")

        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        message = BytesParser(policy=policy.default).parsebytes(header + raw)
        fields: dict[str, str] = {}
        files: list[UploadedFile] = []
        for part in message.iter_parts():
            disposition = part.get("Content-Disposition", "")
            if "form-data" not in disposition:
                continue
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                files.append(UploadedFile(filename=filename, data=payload))
            elif name:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")
        validate_uploaded_files(files)
        return ParsedForm(fields, files)

    def create_batch(self) -> None:
        try:
            form = self.read_form()
        except UploadLimitError as exc:
            self.send_error_json(str(exc), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        except RequestValidationError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return
        name = form.getfirst("name") or time.strftime("批次_%Y%m%d_%H%M%S")
        allow_empty = form.getfirst("allow_empty") == "1"
        files = form.files

        batch = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "created_at": now_ms(),
            "calibration_points": None,
            "stopped": False,
            "images": [],
        }
        for file_item in files:
            add_uploaded_file(batch, file_item)

        if not batch["images"] and not allow_empty:
            self.send_error_json("没有收到可用图片")
            return

        with store_lock:
            stop_open_batches()
            write_batch(batch)
        self.send_json({"batch": batch_summary(batch), "id": batch["id"]}, HTTPStatus.CREATED)

    def add_images(self, batch_id: str) -> None:
        try:
            form = self.read_form()
        except UploadLimitError as exc:
            self.send_error_json(str(exc), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        except RequestValidationError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return
        try:
            batch = read_batch(batch_id)
        except FileNotFoundError:
            self.send_error_json("批次不存在", HTTPStatus.NOT_FOUND)
            return
        if batch.get("stopped"):
            self.send_error_json("本批已停止，请先开始新批次")
            return

        files = form.files
        added = []
        for file_item in files:
            image = add_uploaded_file(batch, file_item)
            if image:
                added.append(image["id"])
        if not added:
            self.send_error_json("没有收到可用图片")
            return
        with store_lock:
            write_batch(batch)
        self.send_json({"added": len(added), "image_ids": added}, HTTPStatus.CREATED)

    def stop_batch(self, batch_id: str) -> None:
        try:
            batch = read_batch(batch_id)
        except FileNotFoundError:
            self.send_error_json("批次不存在", HTTPStatus.NOT_FOUND)
            return
        with store_lock:
            stop_open_batches()
        self.send_json({"ok": True, "summary": batch_summary(batch)})

    def submit_points(self, batch_id: str) -> None:
        try:
            payload = self.read_json_body()
            points = payload["points"]
            mode = payload.get("mode", "one")
            image_id = payload.get("image_id")
            batch = read_batch(batch_id)
        except UploadLimitError as exc:
            self.send_error_json(str(exc), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        except RequestValidationError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return
        except (KeyError, json.JSONDecodeError, FileNotFoundError):
            self.send_error_json("提交内容不完整")
            return

        if image_id:
            targets = [image_id]
        else:
            self.send_error_json("缺少图片")
            return

        queue_images(batch_id, targets, points)
        self.send_json({"queued": len(targets)})

    def exclude_image(self, batch_id: str, image_id: str) -> None:
        try:
            batch = read_batch(batch_id)
            image = next(item for item in batch["images"] if item["id"] == image_id)
        except (FileNotFoundError, StopIteration):
            self.send_error_json("图片不存在", HTTPStatus.NOT_FOUND)
            return

        image["excluded"] = True
        image["status"] = "excluded"
        image["excluded_at"] = now_ms()
        with store_lock:
            write_batch(batch)
        self.send_json({"ok": True, "summary": batch_summary(batch)})

    def correct_image(self, batch_id: str, image_id: str) -> None:
        try:
            payload = self.read_json_body()
            delta = max(-20, min(20, int(payload.get("delta", 0))))
            batch = read_batch(batch_id)
            image = next(item for item in batch["images"] if item["id"] == image_id)
        except UploadLimitError as exc:
            self.send_error_json(str(exc), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        except RequestValidationError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return
        except (ValueError, TypeError, json.JSONDecodeError, FileNotFoundError, StopIteration):
            self.send_error_json("修正内容不完整")
            return

        if not image.get("summary"):
            self.send_error_json("当前图片还没有统计结果")
            return

        image["manual_ng_delta"] = delta
        image["corrected_at"] = now_ms()
        with store_lock:
            write_batch(batch)
        self.send_json({"ok": True, "summary": batch_summary(batch), "defective_chips": corrected_defective_chips(image)})

    def reset_image_correction(self, batch_id: str, image_id: str) -> None:
        try:
            batch = read_batch(batch_id)
            image = next(item for item in batch["images"] if item["id"] == image_id)
        except (FileNotFoundError, StopIteration):
            self.send_error_json("图片不存在", HTTPStatus.NOT_FOUND)
            return

        image["manual_ng_delta"] = 0
        image.pop("corrected_at", None)
        with store_lock:
            write_batch(batch)
        self.send_json({"ok": True, "summary": batch_summary(batch)})

    def preview_grid(self, batch_id: str, image_id: str) -> None:
        try:
            payload = self.read_json_body()
            points = payload["points"]
            batch = read_batch(batch_id)
            image = next(item for item in batch["images"] if item["id"] == image_id)
        except UploadLimitError as exc:
            self.send_error_json(str(exc), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        except RequestValidationError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return
        except (KeyError, json.JSONDecodeError, FileNotFoundError, StopIteration):
            self.send_error_json("预览内容不完整")
            return

        try:
            from wafer_defect_counter import generate_grid_preview_file

            output_dir = batch_dir(batch_id) / "previews" / image_id
            preview_path = generate_grid_preview_file(Path(image["path"]), points, output_dir)
        except Exception as exc:
            self.send_error_json(str(exc))
            return

        image["preview_image"] = preview_path
        image["preview_points"] = points
        with store_lock:
            write_batch(batch)
        self.send_json({"preview_url": media_url(preview_path)})

    def serve_file(self, path: Path) -> None:
        try:
            resolved = resolve_inside(STATIC_DIR, path)
            body = resolved.read_bytes()
        except OSError:
            self.send_error_json("文件不存在", HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(resolved, body)

    def serve_media(self, rel_path: str) -> None:
        try:
            resolved = resolve_inside(DATA_DIR, DATA_DIR / unquote(rel_path))
            body = resolved.read_bytes()
        except OSError:
            self.send_error_json("图片不存在", HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(resolved, body)

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_bytes(self, path: Path, body: bytes) -> None:
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    ensure_dirs()
    host = "0.0.0.0"
    port = 8765
    scheme = "http"
    try:
        server = ThreadingHTTPServer((host, port), AppHandler)
    except OSError:
        port = 8766
        server = ThreadingHTTPServer((host, port), AppHandler)
    if CERT_FILE.exists() and KEY_FILE.exists():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERT_FILE, KEY_FILE)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"网站已启动：{scheme}://127.0.0.1:{port}")
    print(f"手机访问：请使用 {scheme}://电脑IP:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
