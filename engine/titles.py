"""Render declarative title cards to transparent PNG files with Pillow."""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import lib

ACCENT = (37, 99, 235)
WHITE = (255, 255, 255)


def _font(size: int, requested: str | None = None, *, bold: bool = False):
    return ImageFont.truetype(lib.font(requested, bold=bold), size)


def _track(draw, xy, text, font, fill, tracking=0, anchor_center=None):
    x, y = xy
    if anchor_center is not None:
        total = sum(draw.textlength(char, font=font) + tracking for char in text) - tracking
        x = anchor_center - total / 2
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + tracking
    return x


def card(
    name: str,
    *,
    hero: str | None = None,
    kicker: str | None = None,
    caption: str | None = None,
    accent=ACCENT,
    hero_font: str | None = None,
    hero_size: int = 108,
    y: int | None = None,
    shadow: bool = True,
) -> str:
    image = Image.new("RGBA", (lib.W, lib.H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    center = lib.W // 2
    cursor = y if y is not None else int(lib.H * 0.60)

    if kicker:
        kicker_font = _font(max(20, int(lib.H * 0.018)), bold=True)
        tracking = max(2, int(lib.W * 0.0055))
        width = sum(draw.textlength(char, font=kicker_font) + tracking for char in kicker) - tracking
        padding = max(14, int(lib.W * 0.02))
        height = max(42, int(lib.H * 0.03))
        draw.rounded_rectangle(
            [center - width / 2 - padding, cursor - 12, center + width / 2 + padding, cursor + height],
            radius=max(6, int(lib.W * 0.007)), fill=accent,
        )
        _track(draw, (0, cursor), kicker, kicker_font, WHITE, tracking, center)
        cursor += int(lib.H * 0.041)

    if hero:
        hero_face = _font(hero_size, hero_font, bold=True)
        for line in hero.split("\n"):
            if shadow:
                layer = Image.new("RGBA", (lib.W, lib.H), (0, 0, 0, 0))
                layer_draw = ImageDraw.Draw(layer)
                _track(layer_draw, (0, cursor + 6), line, hero_face, (0, 0, 0, 150), -2, center)
                image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(14)))
                draw = ImageDraw.Draw(image)
            _track(draw, (0, cursor), line, hero_face, WHITE, -2, center)
            cursor += int(hero_size * 1.02)

    if caption:
        caption_face = _font(max(30, int(lib.H * 0.03)))
        cursor += int(lib.H * 0.007)
        for line in caption.split("\n"):
            if shadow:
                layer = Image.new("RGBA", (lib.W, lib.H), (0, 0, 0, 0))
                ImageDraw.Draw(layer).text(
                    (center, cursor + 4), line, font=caption_face,
                    fill=(0, 0, 0, 160), anchor="ma",
                )
                image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(10)))
                draw = ImageDraw.Draw(image)
            draw.text(
                (center, cursor), line, font=caption_face,
                fill=(255, 255, 255, 235), anchor="ma",
            )
            cursor += int(lib.H * 0.0365)

    path = os.path.join(lib.CACHE, f"title_{name}.png")
    image.save(path)
    return path


def bottom_bar(name: str, left: str, right: str | None = None, accent=ACCENT) -> str:
    image = Image.new("RGBA", (lib.W, lib.H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    face = _font(max(20, int(lib.H * 0.019)), bold=True)
    y = lib.H - int(lib.H * 0.292)
    side = round(lib.W * 0.0833)
    tracking = max(2, int(lib.W * 0.0037))
    draw.rectangle([side, y + 6, side + 8, y + int(lib.H * 0.023)], fill=accent)
    _track(draw, (side + 22, y), left, face, WHITE, tracking)
    if right:
        width = sum(draw.textlength(char, font=face) + tracking for char in right) - tracking
        _track(draw, (lib.W - side - width, y), right, face, (255, 255, 255, 220), tracking)
    path = os.path.join(lib.CACHE, f"bar_{name}.png")
    image.save(path)
    return path


def render(spec: dict) -> str:
    kind = spec.get("kind", "card")
    accent = tuple(spec.get("accent", ACCENT))
    if kind == "bottom_bar":
        return bottom_bar(spec["name"], spec["left"], spec.get("right"), accent)
    if kind != "card":
        raise ValueError(f"unknown title kind: {kind}")
    y = spec.get("y")
    if isinstance(y, float) and 0 <= y <= 1:
        y = int(lib.H * y)
    return card(
        spec["name"], hero=spec.get("hero"), kicker=spec.get("kicker"),
        caption=spec.get("caption"), accent=accent,
        hero_font=spec.get("hero_font"), hero_size=int(spec.get("hero_size", 108)),
        y=y, shadow=bool(spec.get("shadow", True)),
    )


def render_plan(specs: list[dict]) -> list[dict]:
    plan = []
    for spec in specs:
        item = dict(spec)
        item["path"] = render(spec)
        plan.append(item)
    return plan


def assert_safe(plan: list[dict], safe_zone: dict) -> None:
    left = int(safe_zone.get("side", round(lib.W * 0.0833)))
    right = lib.W - left
    top = int(safe_zone.get("top", round(lib.H * 0.115)))
    bottom = lib.H - int(safe_zone.get("bottom", round(lib.H * 0.25)))
    for spec in plan:
        with Image.open(spec["path"]) as image:
            bbox = image.convert("RGBA").getchannel("A").getbbox()
        if not bbox:
            raise ValueError(f"title is empty: {spec['name']}")
        if bbox[0] < left or bbox[2] > right or bbox[1] < top or bbox[3] > bottom:
            raise ValueError(
                f"title {spec['name']} is outside safe zone: {bbox}; "
                f"allowed {(left, top, right, bottom)}"
            )
