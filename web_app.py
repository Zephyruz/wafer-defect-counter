from __future__ import annotations

import cgi
import json
import mimetypes
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "web_data"
BATCHES_DIR = DATA_DIR / "batches"
STATIC_DIR = ROOT / "web_static"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

executor = ThreadPoolExecutor(max_workers=2)
store_lock = threading.Lock()


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
    return json.loads(path.read_text(encoding="utf-8"))


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


def batch_summary(batch: dict) -> dict:
    images = batch.get("images", [])
    done = [image for image in images if image.get("status") == "done"]
    processing = [image for image in images if image.get("status") == "processing"]
    failed = [image for image in images if image.get("status") == "failed"]
    total_chips = sum(image.get("summary", {}).get("total_chips", 0) for image in done)
    defective_chips = sum(image.get("summary", {}).get("defective_chips", 0) for image in done)
    return {
        "id": batch["id"],
        "name": batch["name"],
        "created_at": batch["created_at"],
        "image_count": len(images),
        "done_count": len(done),
        "processing_count": len(processing),
        "failed_count": len(failed),
        "total_chips": total_chips,
        "defective_chips": defective_chips,
        "defect_rate": defective_chips / total_chips if total_chips else 0,
    }


def safe_filename(name: str) -> str:
    stem = Path(name).stem or "image"
    suffix = Path(name).suffix.lower() or ".jpg"
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    return f"{cleaned[:80]}{suffix}"


def media_url(path: str | Path) -> str:
    rel = Path(path).resolve().relative_to(DATA_DIR.resolve()).as_posix()
    return f"/media/{rel}"


def image_for_response(image: dict) -> dict:
    copied = dict(image)
    copied["url"] = media_url(image["path"])
    summary = copied.get("summary")
    if summary:
        summary = dict(summary)
        summary["result_url"] = media_url(summary["result_image"])
        summary["debug_url"] = media_url(summary["debug_image"])
        if summary.get("chip_report"):
            summary["chip_report_url"] = media_url(summary["chip_report"])
        copied["summary"] = summary
    return copied


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
        shutil.copyfileobj(file_item.file, output)
    image = {
        "id": image_id,
        "filename": file_item.filename,
        "path": str(target),
        "status": "waiting_points",
        "points": None,
        "summary": None,
        "error": None,
        "created_at": now_ms(),
    }
    batch["images"].append(image)
    return image


def process_image(batch_id: str, image_id: str, points: list[list[float]]) -> None:
    with store_lock:
        batch = read_batch(batch_id)
        image = next(item for item in batch["images"] if item["id"] == image_id)
        image["status"] = "processing"
        image["points"] = points
        image["error"] = None
        write_batch(batch)

    try:
        from wafer_defect_counter import process_image_file

        output_dir = batch_dir(batch_id) / "results" / image_id
        summary = process_image_file(Path(image["path"]), points, output_dir)
        summary_dict = asdict(summary)
        status = "done"
        error = None
    except Exception as exc:
        summary_dict = None
        status = "failed"
        error = str(exc)

    with store_lock:
        batch = read_batch(batch_id)
        image = next(item for item in batch["images"] if item["id"] == image_id)
        image["status"] = status
        image["summary"] = summary_dict
        image["error"] = error
        image["finished_at"] = now_ms()
        write_batch(batch)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "WaferWeb/0.2"

    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
        if path.startswith("/static/"):
            self.serve_file(STATIC_DIR / unquote(path.removeprefix("/static/")))
            return
        if path.startswith("/media/"):
            self.serve_media(path.removeprefix("/media/"))
            return
        if path == "/api/batches":
            self.send_json({"batches": list_batches()})
            return
        if path.startswith("/api/batches/"):
            batch_id = path.split("/", 3)[3]
            try:
                batch = read_batch(batch_id)
            except FileNotFoundError:
                self.send_error_json("批次不存在", HTTPStatus.NOT_FOUND)
                return
            batch = dict(batch)
            batch["images"] = [image_for_response(image) for image in batch["images"]]
            batch["summary"] = batch_summary(batch)
            self.send_json(batch)
            return
        self.send_error_json("页面不存在", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/batches":
            self.create_batch()
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
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def read_form(self) -> cgi.FieldStorage:
        return cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST"})

    def create_batch(self) -> None:
        form = self.read_form()
        name = form.getfirst("name") or time.strftime("批次_%Y%m%d_%H%M%S")
        files = form["files"] if "files" in form else []
        if not isinstance(files, list):
            files = [files]

        batch = {"id": uuid.uuid4().hex[:12], "name": name, "created_at": now_ms(), "images": []}
        for file_item in files:
            add_uploaded_file(batch, file_item)

        if not batch["images"]:
            self.send_error_json("没有收到可用图片")
            return

        with store_lock:
            write_batch(batch)
        self.send_json({"batch": batch_summary(batch), "id": batch["id"]}, HTTPStatus.CREATED)

    def add_images(self, batch_id: str) -> None:
        try:
            form = self.read_form()
            batch = read_batch(batch_id)
        except FileNotFoundError:
            self.send_error_json("批次不存在", HTTPStatus.NOT_FOUND)
            return

        files = form["files"] if "files" in form else []
        if not isinstance(files, list):
            files = [files]
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

    def submit_points(self, batch_id: str) -> None:
        try:
            payload = self.read_json_body()
            points = payload["points"]
            mode = payload.get("mode", "one")
            image_id = payload.get("image_id")
            batch = read_batch(batch_id)
        except (KeyError, json.JSONDecodeError, FileNotFoundError):
            self.send_error_json("提交内容不完整")
            return

        if mode == "batch":
            targets = [image["id"] for image in batch["images"] if image.get("status") != "processing"]
        elif image_id:
            targets = [image_id]
        else:
            self.send_error_json("缺少图片")
            return

        for target_id in targets:
            executor.submit(process_image, batch_id, target_id, points)
        self.send_json({"queued": len(targets)})

    def serve_file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            if STATIC_DIR.resolve() not in resolved.parents and resolved != STATIC_DIR.resolve() / "index.html":
                raise FileNotFoundError
            body = resolved.read_bytes()
        except OSError:
            self.send_error_json("文件不存在", HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(resolved, body)

    def serve_media(self, rel_path: str) -> None:
        try:
            resolved = (DATA_DIR / unquote(rel_path)).resolve()
            if DATA_DIR.resolve() not in resolved.parents:
                raise FileNotFoundError
            body = resolved.read_bytes()
        except OSError:
            self.send_error_json("图片不存在", HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(resolved, body)

    def send_bytes(self, path: Path, body: bytes) -> None:
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    ensure_dirs()
    host = "0.0.0.0"
    port = 8765
    try:
        server = ThreadingHTTPServer((host, port), AppHandler)
    except OSError:
        port = 8766
        server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"网站已启动：http://127.0.0.1:{port}")
    print(f"手机访问：请使用 http://电脑IP:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
