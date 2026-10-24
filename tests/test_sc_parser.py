import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scanner-engine"))
import sc_parser


def make_ocr_data(words):
    data = {key: [] for key in ("text", "conf", "left", "top", "width", "height")}
    for text, x, y, width in words:
        data["text"].append(text)
        data["conf"].append("90")
        data["left"].append(x)
        data["top"].append(y)
        data["width"].append(width)
        data["height"].append(18)
    return data


class ScanParserTests(unittest.TestCase):
    def tab_fixture(self, active_tab):
        image = np.zeros((400, 1000, 3), dtype=np.uint8)
        blue = cv2.cvtColor(np.uint8([[[90, 200, 200]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        if active_tab == "buy":
            cv2.rectangle(image, (630, 55), (745, 105), blue, -1)
        else:
            cv2.rectangle(image, (755, 55), (970, 105), blue, -1)
        data = make_ocr_data([
            ("YOUR", 10, 15, 55),
            ("INVENTORIES", 70, 15, 120),
            ("IN", 10, 75, 25),
            ("DEMAND", 40, 75, 80),
            ("SHOP", 580, 15, 60),
            ("INVENTORY", 645, 15, 110),
            ("BUY", 655, 72, 45),
            ("LOCAL", 780, 72, 70),
            ("MARKET", 855, 72, 75),
            ("VALUE", 935, 72, 60),
            ("MAX", 600, 145, 40),
            ("INVENTORY", 645, 145, 110),
        ])
        return image, sc_parser.group_lines(data)

    def test_active_tab_uses_button_highlight_not_shared_inventory_header(self):
        for active_tab in ("buy", "sell"):
            with self.subTest(active_tab=active_tab):
                image, lines = self.tab_fixture(active_tab)
                self.assertEqual(
                    sc_parser._detect_tab(
                        lines,
                        color_image=image,
                        proc_w=image.shape[1],
                        proc_h=image.shape[0],
                        right_min=450,
                    ),
                    active_tab,
                )

    def test_left_panel_demand_label_does_not_override_active_buy_tab(self):
        image, lines = self.tab_fixture("buy")
        self.assertEqual(
            sc_parser._detect_tab(
                lines,
                color_image=image,
                proc_w=image.shape[1],
                proc_h=image.shape[0],
                right_min=450,
            ),
            "buy",
        )

    def test_active_tab_color_works_when_ocr_misses_tab_labels(self):
        for active_tab in ("buy", "sell"):
            with self.subTest(active_tab=active_tab):
                image, lines = self.tab_fixture(active_tab)
                header_only = [
                    line for line in lines
                    if any("SHOP INVENTORY" in segment["text"] for segment in line["segments"])
                ]
                self.assertEqual(
                    sc_parser._detect_tab(
                        header_only,
                        color_image=image,
                        proc_w=image.shape[1],
                        proc_h=image.shape[0],
                        right_min=450,
                    ),
                    active_tab,
                )

    def test_modal_values_require_two_matching_ocr_passes(self):
        def candidate(value):
            return 50, {
                "layout": "modal",
                "tab": "buy",
                "rows": [{
                    "commodity": "Silicon",
                    "buy": value,
                    "sell": None,
                    "stock": 735,
                    "demand": None,
                }],
            }

        diagnostics = {}
        merged = sc_parser._merge_results(
            [candidate(1964), candidate(1964), candidate(1965)],
            active_tab="buy",
            diagnostics=diagnostics,
        )
        self.assertEqual(merged[0]["buy"], 1964)
        self.assertEqual(merged[0]["stock"], 735)

        disputed = sc_parser._merge_results(
            [candidate(1964), candidate(1965)],
            active_tab="buy",
        )
        self.assertIsNone(disputed[0]["buy"])

        single_read = sc_parser._merge_results(
            [candidate(1964)],
            active_tab="buy",
        )
        self.assertIsNone(single_read[0]["buy"])

    def test_modal_values_are_rejected_when_active_tab_is_ambiguous(self):
        parsed = {
            "layout": "modal",
            "tab": None,
            "rows": [{
                "commodity": "Silicon",
                "buy": 1964,
                "sell": None,
                "stock": 735,
                "demand": None,
            }],
        }
        self.assertEqual(
            sc_parser._merge_results([(50, parsed), (50, parsed)], active_tab=None),
            [],
        )

    def test_live_change_ratio_ignores_identical_frames_and_detects_changed_frames(self):
        first = np.zeros((64, 96), dtype=np.uint8)
        same = first.copy()
        changed = first.copy()
        changed[10:20, 10:20] = 255
        self.assertEqual(sc_parser.image_change_ratio(first, same), 0)
        self.assertGreater(sc_parser.image_change_ratio(first, changed), 0.0015)

    def test_quality_report_flags_unconfirmed_tab_and_disputed_fields(self):
        report = sc_parser.quality_report({
            "layout": "modal",
            "diagnostics": {
                "errors": [{"error": "The active Buy/Local Market Value tab could not be confirmed."}],
                "field_votes": {"Silicon": {"buy": {"accepted": False}}},
                "passes": [{}, {}],
            },
        })
        self.assertTrue(report["review_recommended"])
        self.assertEqual(report["ocr_passes"], 2)
        self.assertIn("The active kiosk tab could not be confirmed.", report["warnings"])
        self.assertIn("One or more values lacked agreement across OCR passes.", report["warnings"])

    def test_left_panel_probe_recognizes_inventory_header(self):
        image = np.zeros((80, 120, 3), dtype=np.uint8)
        fake_data = {"text": ["YOUR", "INVENTORIES", "IN", "DEMAND"]}
        with patch.object(sc_parser.pytesseract, "image_to_data", return_value=fake_data):
            diagnostics = {}
            result = sc_parser._probe_left_panel(image, diagnostics)
        self.assertIsNotNone(result)
        self.assertTrue(diagnostics["left_panel"]["detected"])

    def test_ocr_overlay_maps_cropped_scaled_coordinates_to_capture(self):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        data = make_ocr_data([("WASTE", 50, 50, 40)])
        annotated = sc_parser.annotate(
            image,
            data,
            scale={"x": 5, "y": 5},
            offset_x=100,
        )
        self.assertTupleEqual(tuple(annotated[10, 110]), (0, 200, 0))
        self.assertTupleEqual(tuple(annotated[10, 50]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
