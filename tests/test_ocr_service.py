import base64
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ocr"))

from scip_ocr.main import app


class OcrServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_invalid_base64_is_rejected(self):
        response = self.client.post("/ocr/scan", json={"image_base64": "not base64!"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "image_base64 is not valid base64.")

    def test_small_valid_image_is_rejected_before_ocr(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (100, 100)).save(image_buffer, format="PNG")
        image_data = base64.b64encode(image_buffer.getvalue()).decode("ascii")

        response = self.client.post("/ocr/scan", json={"image_base64": image_data})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Image dimensions are too small.")

    def test_ocr_engine_unavailable_is_not_reported_as_an_empty_success(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (320, 240)).save(image_buffer, format="PNG")
        image_data = base64.b64encode(image_buffer.getvalue()).decode("ascii")
        parsed = {
            "rows": [],
            "diagnostics": {"passes": [], "errors": [{"error": "TesseractNotFoundError"}]},
        }

        with patch("scip_ocr.main.sc_parser.run_ocr", return_value=(None, parsed, "none")):
            response = self.client.post("/ocr/scan", json={"image_base64": image_data})

        self.assertEqual(response.status_code, 503)
        self.assertIn("Tesseract OCR", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
