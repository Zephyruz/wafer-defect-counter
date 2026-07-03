from __future__ import annotations

import argparse
import mimetypes
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def multipart_post(url: str, fields: dict[str, str], files: list[Path]) -> dict:
    boundary = f"----wafer{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for path in files:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(path.read_bytes())
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode())
    request = Request(url, data=bytes(body), method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Content-Length", str(len(body)))
    with urlopen(request, timeout=30) as response:
        import json

        return json.loads(response.read().decode("utf-8"))


def wait_until_stable(path: Path, stable_seconds: float) -> bool:
    last_size = -1
    stable_since = time.time()
    while True:
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size != last_size:
            last_size = size
            stable_since = time.time()
        elif time.time() - stable_since >= stable_seconds:
            return True
        time.sleep(0.2)


def create_batch(base_url: str, name: str, first_file: Path) -> str:
    payload = multipart_post(f"{base_url}/api/batches", {"name": name}, [first_file])
    return payload["id"]


def upload_to_batch(base_url: str, batch_id: str, image_path: Path) -> None:
    multipart_post(f"{base_url}/api/batches/{batch_id}/images", {}, [image_path])


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch a camera output folder and upload new photos to the wafer website.")
    parser.add_argument("folder", type=Path, help="相机或生产线软件保存图片的文件夹")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="网站地址")
    parser.add_argument("--batch-id", default="", help="已有批次 ID；不填则用第一张图自动新建批次")
    parser.add_argument("--batch-name", default=time.strftime("生产批次_%Y%m%d_%H%M%S"), help="自动新建批次名称")
    parser.add_argument("--stable-seconds", type=float, default=1.0, help="文件大小稳定多久后再上传")
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not folder.exists():
        raise FileNotFoundError(folder)

    seen = {path.resolve() for path in folder.iterdir() if path.is_file()}
    batch_id = args.batch_id.strip()
    print(f"正在监听：{folder}")
    print(f"网站地址：{args.url}")
    if batch_id:
        print(f"上传到已有批次：{batch_id}")
    else:
        print("第一张新图片会自动创建批次")

    while True:
        candidates = sorted(
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and path.resolve() not in seen
        )
        for path in candidates:
            resolved = path.resolve()
            seen.add(resolved)
            if not wait_until_stable(path, args.stable_seconds):
                continue
            try:
                if not batch_id:
                    batch_id = create_batch(args.url.rstrip("/"), args.batch_name, path)
                    print(f"已创建批次：{batch_id}，上传：{path.name}")
                else:
                    upload_to_batch(args.url.rstrip("/"), batch_id, path)
                    print(f"已上传：{path.name}")
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                print(f"上传失败：{path.name}，{exc}")
                seen.discard(resolved)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
