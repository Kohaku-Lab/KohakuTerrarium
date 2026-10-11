"""Unit tests for ``llm/request_budget.py``: measuring, fitting and the learned ceiling."""

import base64
import io
import random

from PIL import Image

from kohakuterrarium.llm.image_preparation import body_bytes
from kohakuterrarium.llm.request_budget import (
    MIN_REQUEST_BYTES,
    RequestCeiling,
    RequestMeasure,
    effective_target,
    fit_request,
)


def _noise_url(seed, size=(700, 700)):
    pixels = random.Random(seed).randbytes(size[0] * size[1] * 3)
    stream = io.BytesIO()
    Image.frombytes("RGB", size, pixels).save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def _image_body(seed=7):
    return {
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": _noise_url(seed)}],
            }
        ]
    }


def test_fit_request_compresses_to_the_target_and_measures_the_wire_body():
    body = _image_body()
    initial = body_bytes(body)
    fitted, measure = fit_request(body, initial // 3, provider="test")
    assert measure == RequestMeasure(body_bytes(fitted), 1)
    assert measure.bytes <= initial // 3


def test_disabled_target_leaves_the_body_alone_but_still_measures_it():
    body = _image_body()
    initial = body_bytes(body)
    for disabled in (None, 0):
        same, measure = fit_request(body, disabled)
        assert same is body
        assert measure == RequestMeasure(initial, None)


def test_text_only_body_reports_zero_images():
    text = {"input": [{"role": "user", "content": "hi"}]}
    fitted, measure = fit_request(text, 10**6)
    assert fitted is text
    assert measure == RequestMeasure(body_bytes(text), 0)


def test_request_ceiling_only_ever_lowers_and_has_a_floor():
    ceiling = RequestCeiling()
    assert ceiling.max_bytes is None
    assert ceiling.lower(4_000_000) == 3_000_000
    assert ceiling.lower(8_000_000) == 3_000_000
    assert ceiling.lower(2_000_000) == 1_500_000
    assert ceiling.max_bytes == 1_500_000
    assert ceiling.lower(10) == MIN_REQUEST_BYTES
    assert ceiling.lower(10) == MIN_REQUEST_BYTES


def test_effective_target_takes_the_tighter_bound():
    assert effective_target(None, None) is None
    assert effective_target(0, None) is None
    assert effective_target(100, None) == 100
    assert effective_target(None, 50) == 50
    assert effective_target(100, 50) == 50
    assert effective_target(40, 50) == 40
