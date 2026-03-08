from google import genai
from google.genai import types
import time
import json
from datetime import datetime

#  `GEMINI_API_KEY`.
class Gemini:
    def __init__(self):
        self.client = genai.Client(api_key="PUT YOUR API KEY HERE")
        self.data_image_location = "PUT YOUR DATA IMAGE LOCATION HERE"
        self.item_list = ["apple", "banana", "orange", "milk", "bread", "egg", "cheese", "butter", "juice", "water"]
        self.timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.action_id = f"act_{self.timestamp}"
        self.demo_format = {
            "session_id" : f"session_{self.timestamp}",
            "timestamp" : self.timestamp,
        }
    def run(self):
        with open(self.data_image_location, "rb") as f:
            image_bytes = f.read()
        response = self.client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                ),
                ("Identify the item in the picture (if the item is in the list: " + str(self.item_list) +
                    "), if not, say 'unknown'. ")            
                ]
        )
        self.item_name = response.text
        self.timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.action_id = f"act_{self.timestamp}"
        # Build detections from Gemini response (one detection per item name for now)
        detection = {
            "class_name": self.item_name.strip() if self.item_name else "unknown",
            "confidence": 0.9,
            "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
            "center": {"x": 0, "y": 0},
            "track_id": 1,
            "in_roi": True,
        }
        iso_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.demo_format = {
            "session_id": f"session_{self.timestamp}",
            "metadata": {
                "camera_model": "Raspberry Pi Camera Module v2",
                "resolution": "640x480",
                "fps": 2,
                "yolo_model": "gemini-3-flash-preview",
                "confidence_threshold": 0.5,
            },
            "samples": [
                {
                    "frame_path": f"{self.data_image_location}",
                    "index": 0,
                    "timestamp": iso_ts,
                    "timestamp_ms": 0,
                    "detections": [detection],
                    "frame_hash": "",
                }
            ],
            "roi_definition": {
                "name": "fridge_interior",
                "coordinates": {
                    "x": 200,
                    "y": 100,
                    "width": 300,
                    "height": 350,
                },
            },
        }
        return self.demo_format, response.text

def save_demo_format(demo_format, path="demo_format.json", append=True):
    """Write demo_format to JSON. If append=True and file exists, merge new samples into existing file."""
    if append:
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            # Merge: append new samples to existing samples
            existing["samples"] = existing.get("samples", []) + demo_format.get("samples", [])
            # Optionally update session_id/timestamp to latest
            existing["session_id"] = demo_format.get("session_id", existing.get("session_id"))
            demo_format = existing
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # No existing file or empty/invalid JSON, write demo_format as-is
    with open(path, "w", encoding="utf-8") as f:
        json.dump(demo_format, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    gemini = Gemini()
    demo_format, response_text = gemini.run()
    save_demo_format(demo_format, append=True)  # set append=False to overwrite