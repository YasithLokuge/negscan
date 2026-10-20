"""Checks on the icon assets. No Qt and no hardware needed."""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")
SVG = os.path.join(ASSETS, "icon.svg")
SVG_NS = "{http://www.w3.org/2000/svg}"


class IconTests(unittest.TestCase):
    def test_svg_is_well_formed(self):
        """Catches the easy mistake: an XML comment may not contain a double hyphen.

        Qt reports it only as 'Expected >, but got ...' at render time, and QSvgRenderer then
        quietly produces an empty icon.
        """
        ET.parse(SVG)

    def test_svg_has_a_square_viewbox(self):
        root = ET.parse(SVG).getroot()
        x, y, w, h = (float(v) for v in root.get("viewBox").split())
        self.assertEqual((x, y), (0.0, 0.0))
        self.assertEqual(w, h, "a non-square icon is letterboxed in the Dock")

    def test_no_clip_paths(self):
        """Qt's SVG engine ignores nested clipPaths and paints the layers over each other."""
        root = ET.parse(SVG).getroot()
        self.assertEqual([], root.findall(f".//{SVG_NS}clipPath"))
        with_clip = [el.tag for el in root.iter() if el.get("clip-path")]
        self.assertEqual([], with_clip)

    def test_every_gradient_reference_resolves(self):
        root = ET.parse(SVG).getroot()
        defined = {el.get("id") for el in root.iter() if el.get("id")}
        for el in root.iter():
            fill = el.get("fill") or ""
            if fill.startswith("url(#"):
                self.assertIn(fill[5:-1], defined, f"{fill} is not defined")

    def test_rendered_png_exists_and_is_square(self):
        png = os.path.join(ASSETS, "icon.png")
        self.assertTrue(os.path.exists(png), "run scripts/make_icon.py")
        import cv2
        image = cv2.imread(png, cv2.IMREAD_UNCHANGED)
        self.assertIsNotNone(image)
        self.assertEqual(image.shape[0], image.shape[1])
        self.assertGreaterEqual(image.shape[0], 512)

    def test_rendered_png_is_not_blank(self):
        """An SVG that fails to parse still renders: it comes out as a transparent square."""
        import cv2
        image = cv2.imread(os.path.join(ASSETS, "icon.png"), cv2.IMREAD_UNCHANGED)
        self.assertEqual(image.shape[2], 4, "expected an alpha channel")
        self.assertGreater(float(image[..., 3].mean()), 128, "icon is mostly transparent")
        self.assertGreater(float(image[..., :3].std()), 20, "icon has no contrast")


if __name__ == "__main__":
    unittest.main()
