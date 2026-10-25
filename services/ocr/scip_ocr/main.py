import base64
import binascii
from io import BytesIO

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

import sc_parser

MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_base64: str = Field(min_length=1, max_length=28_000_000)


app = FastAPI(
    title="SCIP OCR Service",
    version="2.0.0",
    description="Processes player-submitted screenshots locally; does not upload reports.",
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "scip-ocr"}


@app.post("/ocr/scan")
def scan_image(request: ScanRequest):
    try:
        image_bytes = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="image_base64 is not valid base64.") from exc
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Screenshot exceeds the 20 MiB limit.")

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=413, detail="Image dimensions exceed the supported limit.") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Image could not be decoded.") from exc
    if width * height > 40_000_000:
        raise HTTPException(status_code=413, detail="Image dimensions exceed the supported limit.")
    if height < 200 or width < 200:
        raise HTTPException(status_code=422, detail="Image dimensions are too small.")

    encoded = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=422, detail="Image could not be decoded.")

    try:
        _, parsed, tag = sc_parser.run_ocr(image)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OCR processing failed: {type(exc).__name__}.") from exc

    return {
        "schema_version": "2.0",
        "source": "player-scan",
        "tag": tag,
        "layout": parsed.get("layout", "unknown"),
        "tab": parsed.get("tab"),
        "commodities": parsed.get("rows", []),
        "inventory": parsed.get("inventory", []),
        "quality": sc_parser.quality_report(parsed),
        "diagnostics": parsed.get("diagnostics", {}),
        "requires_user_confirmation": True,
        "automatically_uploaded": False,
    }
