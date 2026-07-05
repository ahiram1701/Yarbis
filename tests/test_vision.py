import base64
import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import memory
import tools
import vision


class VisionStateTestCase(unittest.TestCase):
    def test_default_vision(self):
        v = memory.default_state()["vision"]
        self.assertEqual(v["model"], "gemma3:12b-cloud")
        self.assertEqual(v["max_image_dim"], 1280)
        self.assertEqual(v["timeout_seconds"], 120)

    def test_normalize_clamps_and_default_model(self):
        n = memory.normalize_state({"vision": {"model": "  ", "max_image_dim": 10, "timeout_seconds": 99999}})["vision"]
        self.assertEqual(n["model"], "gemma3:12b-cloud")
        self.assertEqual(n["max_image_dim"], 256)
        self.assertEqual(n["timeout_seconds"], 600)


class VisionEncodeTestCase(unittest.TestCase):
    def test_encode_downscales_large_image(self):
        big = Image.new("RGB", (4000, 2000), "white")
        buf = io.BytesIO()
        big.save(buf, format="PNG")
        encoded = vision._encode_image(buf.getvalue(), 1280)
        self.assertIsInstance(encoded, str)
        out = Image.open(io.BytesIO(base64.b64decode(encoded)))
        self.assertLessEqual(max(out.size), 1280)


class VisionAnalyzeTestCase(unittest.TestCase):
    def test_analyze_image_calls_model_with_image(self):
        fake_response = MagicMock()
        fake_response.message.content = "una imagen de prueba"
        fake_client = MagicMock()
        fake_client.chat.return_value = fake_response

        img = Image.new("RGB", (60, 40), "red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        with patch("ollama.Client", return_value=fake_client):
            with patch.object(vision, "load_state", return_value=memory.default_state()):
                result = vision.analyze_image(buf.getvalue(), "que es?")

        self.assertEqual(result, "una imagen de prueba")
        _, kwargs = fake_client.chat.call_args
        self.assertEqual(kwargs["model"], "gemma3:12b-cloud")
        self.assertIn("images", kwargs["messages"][0])
        self.assertEqual(len(kwargs["messages"][0]["images"]), 1)

    def test_analyze_image_wraps_model_error(self):
        fake_client = MagicMock()
        fake_client.chat.side_effect = RuntimeError("boom")
        img = Image.new("RGB", (10, 10), "blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        with patch("ollama.Client", return_value=fake_client):
            with patch.object(vision, "load_state", return_value=memory.default_state()):
                with self.assertRaises(vision.VisionError):
                    vision.analyze_image(buf.getvalue())


class ToolsAnalyzeImageTestCase(unittest.TestCase):
    def test_missing_file(self):
        self.assertIn("No encontre", tools.analyze_image("no_existe_xyz_123.jpg"))

    def test_empty_path(self):
        self.assertIn("Indica la ruta", tools.analyze_image(""))

    def test_calls_vision_for_existing_file(self):
        path = os.path.join(tempfile.gettempdir(), "_yarbis_vtest.png")
        Image.new("RGB", (12, 12), "green").save(path)
        try:
            with patch.object(tools.vision, "analyze_image", return_value="descripcion ok") as mocked:
                out = tools.analyze_image(path, "que hay?")
            self.assertEqual(out, "descripcion ok")
            mocked.assert_called_once()
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
