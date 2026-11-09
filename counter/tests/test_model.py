from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from counter.model import PersonCounter, draw_boxes, nms

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture(scope="module")
def counter():
    return PersonCounter()


@pytest.mark.parametrize("name,expected", [("bus.jpg", 4), ("zidane.jpg", 2)])
def test_counts_people_in_samples(counter, name, expected):
    assert counter.count(Image.open(SAMPLES / name)).count == expected


def test_accepts_bytes(counter):
    assert counter.count((SAMPLES / "zidane.jpg").read_bytes()).count == 2


def test_empty_image_counts_zero(counter):
    assert counter.count(Image.new("RGB", (640, 480), (40, 40, 40))).count == 0


def test_boxes_are_in_original_image_coordinates(counter):
    img = Image.open(SAMPLES / "zidane.jpg")
    for d in counter.count(img).boxes:
        assert -2 <= d.x1 < d.x2 <= img.width + 2
        assert -2 <= d.y1 < d.y2 <= img.height + 2


def test_nms_drops_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=float)
    scores = np.array([0.9, 0.8, 0.7])
    assert nms(boxes, scores, 0.45) == [0, 2]


def test_draw_boxes_keeps_size(counter):
    img = Image.open(SAMPLES / "bus.jpg")
    assert draw_boxes(img, counter.count(img)).size == img.size
