"""Addresses other devices on the home network can use to reach this PC."""

import io
import socket

import qrcode
import qrcode.image.svg


def local_ip_addresses() -> list[str]:
    """This PC's addresses on the local network (e.g. 192.168.1.20), best guess first."""
    found: list[str] = []
    try:
        # Connecting a UDP socket sends nothing; it just asks Windows which address
        # it would use to reach the internet, which is the Wi-Fi/Ethernet address.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    usable = [ip for ip in found if not ip.startswith(("127.", "169.254.", "0."))]
    return list(dict.fromkeys(usable))


def phone_urls(port: int) -> list[str]:
    return [f"http://{ip}:{port}" for ip in local_ip_addresses()]


def qr_svg(text: str) -> str:
    """A QR code as inline SVG, for scanning with a phone camera."""
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    svg = buffer.getvalue().decode()
    return svg[svg.index("<svg"):]  # drop the XML declaration so it can sit inside HTML
