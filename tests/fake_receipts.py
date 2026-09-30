"""Draw fake receipt photos for tests: dark 'table' background, white slip, printed text."""

import io

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HOME_DEPOT_LINES = [
    "THE HOME DEPOT",
    "123 FAKE STREET",
    "SPRINGFIELD, IL 62701",
    "(555) 555-0100",
    "",
    "0612 00041 23456  09/28/26  02:14 PM",
    "",
    "2X4 STUD 8FT              29.88",
    "  12 @ 2.49",
    "DECK SCREWS 1LB            9.97",
    "PAINT ROLLER KIT          14.98",
    "",
    "SUBTOTAL                  54.83",
    "SALES TAX                  4.39",
    "TOTAL                    $59.22",
    "",
    "VISA XXXX1234 USD$        59.22",
    "AUTH CODE 012345",
    "THANK YOU FOR SHOPPING",
]


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def make_receipt_photo(lines: list[str] = HOME_DEPOT_LINES, *, rotate: float = 0.0, fmt: str = "JPEG") -> bytes:
    font = _font(30)
    line_height = 42
    slip_width = 760
    slip = Image.new("RGB", (slip_width, 80 + line_height * len(lines)), (250, 250, 246))
    draw = ImageDraw.Draw(slip)
    for i, line in enumerate(lines):
        draw.text((40, 40 + i * line_height), line, fill=(30, 30, 30), font=font)
    slip = slip.filter(ImageFilter.GaussianBlur(0.6))  # a little camera softness

    photo = Image.new("RGB", (slip.width + 240, slip.height + 240), (45, 52, 60))
    photo.paste(slip, (120, 120))
    if rotate:
        photo = photo.rotate(rotate, expand=True, fillcolor=(45, 52, 60))
    buffer = io.BytesIO()
    photo.save(buffer, fmt)
    return buffer.getvalue()
