# Wafer Defect Counter

Local web tool for wafer defect counting. The project combines a Python/OpenCV image-processing pipeline with a browser-based workflow for batch upload, camera capture, four-point calibration, grid preview, OK/NG counting, manual correction, and result export.

This repository is intended to stay private because the project may involve company workflow details and production images.

## Features

- Python + OpenCV wafer image analysis
- Local web UI for batch management
- Four-point chip calibration and grid preview
- Automatic OK/NG counting and defect-rate summary
- Per-image annotated outputs and per-chip CSV reports
- Manual NG correction for on-site review
- Phone/PDA upload workflow
- HTTPS live-camera page for continuous capture
- PDA camera selection and startup modes for device compatibility

## Main Files

- `wafer_defect_counter.py` - core image-processing and chip counting logic
- `web_app.py` - local web server and API
- `web_static/` - browser UI files
- `generate_https_cert.py` - local HTTPS certificate generator for camera testing
- `requirements.txt` - Python dependencies
- Chinese run notes and project-summary documents are included in the repository root.

## Setup

Install dependencies in the project virtual environment:

```powershell
cd "<project-folder>"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If the existing virtual environment has path issues, create a new environment and install the same requirements.

## Run

Start the local web app:

```powershell
cd "<project-folder>"
.\.venv\Scripts\python.exe -u web_app.py
```

Desktop access:

```text
https://127.0.0.1:8765
```

Phone/PDA access on the same network:

```text
https://<computer-ip>:8765
https://<computer-ip>:8765/live-capture
```

The `/live-capture` page is used for continuous browser-camera upload. It includes camera selection, startup modes, zoom control, a large capture button, and a system-camera fallback.

## HTTPS Certificate

Browser live-camera access usually requires HTTPS. Generate a local test certificate:

```powershell
.\.venv\Scripts\python.exe generate_https_cert.py
```

Then restart `web_app.py`. Mobile browsers may show a certificate warning for local self-signed certificates.

## Data Safety

Do not commit production images, generated result images, CSV outputs, Excel files, local web data, or HTTPS private keys.

The `.gitignore` is configured to exclude common local data and generated files, including:

- input/output images
- CSV and spreadsheet files
- `web_data/`
- `test results/`
- local archive folders
- `cert.pem` and `key.pem`

## Version Notes

Important Git milestones:

- `85b9d40` - stable on-site version with web UI and manual correction workflow
- `cae4070` - HTTPS live-camera upload and continuous capture workflow
- latest PDA optimization - PDA camera selection and live-camera layout improvements
