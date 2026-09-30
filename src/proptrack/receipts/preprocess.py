"""Clean up a phone photo so Tesseract can read it.

Steps: find the receipt (its four corners, or else the white paper against the
background) and flatten/crop it -> grayscale -> scale so text is a readable size ->
boost contrast -> (one variant) black-and-white threshold.

Cropping matters: background texture such as wood grain turns into junk characters.
"""

import cv2
import numpy as np
from PIL import Image

TARGET_WIDTH = 1400  # Tesseract reads best when characters are ~25-35 px tall
DETECT_EDGE = 800  # edge detection runs on a downscaled copy for speed


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Return corners as top-left, top-right, bottom-right, bottom-left."""
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).ravel()
    return np.array(
        [points[np.argmin(sums)], points[np.argmin(diffs)], points[np.argmax(sums)], points[np.argmax(diffs)]],
        dtype="float32",
    )


def find_receipt_corners(gray: np.ndarray) -> np.ndarray | None:
    """Find the receipt as the largest four-sided shape covering a good part of the photo."""
    height, width = gray.shape
    scale = min(1.0, DETECT_EDGE / max(height, width))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    edges = cv2.Canny(cv2.GaussianBlur(small, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = 0.2 * small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) == 4 and cv2.contourArea(approx) >= min_area:
            return _order_corners(approx.reshape(4, 2).astype("float32") / scale)
    return None


def find_paper_corners(image: Image.Image) -> np.ndarray | None:
    """Find the receipt as the largest white, colourless area (e.g. on a wooden table).

    Used when the receipt's four corners aren't all in the photo. Returns the corners
    of the rotated rectangle around the paper, or None if the paper can't be told
    apart from the background (e.g. a white table).
    """
    rgb = np.array(image)
    height, width = rgb.shape[:2]
    scale = min(1.0, DETECT_EDGE / max(height, width))
    if scale < 1:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    paper = ((hsv[..., 1] < 60) & (hsv[..., 2] > 140)).astype(np.uint8) * 255
    kernel = np.ones((15, 15), np.uint8)
    paper = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, kernel)  # fill printed text and logos
    paper = cv2.morphologyEx(paper, cv2.MORPH_OPEN, kernel)  # drop small bright specks
    contours, _ = cv2.findContours(paper, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    share = cv2.contourArea(largest) / (paper.shape[0] * paper.shape[1])
    if not 0.15 <= share <= 0.9:  # too small to be the receipt, or it's the whole photo
        return None
    corners = cv2.boxPoints(cv2.minAreaRect(largest)) / scale
    corners[:, 0] = corners[:, 0].clip(0, width - 1)
    corners[:, 1] = corners[:, 1].clip(0, height - 1)
    return _order_corners(corners.astype("float32"))


def flatten(gray: np.ndarray, corners: np.ndarray) -> np.ndarray:
    top_left, top_right, bottom_right, bottom_left = corners
    width = int(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)))
    height = int(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(corners, target)
    return cv2.warpPerspective(gray, matrix, (width, height))


def prepare_variants(image: Image.Image) -> list[np.ndarray]:
    """Return cleaned-up versions of the photo to try OCR on (best one wins)."""
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    corners = find_receipt_corners(gray)
    if corners is None:
        corners = find_paper_corners(image)
    if corners is not None:
        gray = flatten(gray, corners)

    scale = TARGET_WIDTH / gray.shape[1]
    if abs(scale - 1) > 0.1:
        interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interpolation)

    contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    binary = cv2.adaptiveThreshold(
        cv2.GaussianBlur(contrast, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return [binary, contrast]
