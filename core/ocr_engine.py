import io
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2
import numpy as np
import pytesseract
from PIL import Image


def _remove_small_components(binary_img):
    try:
        h, w = binary_img.shape[:2]
        min_area = max(40, int(0.0005 * h * w))
        min_w, min_h = 8, 8

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary_img, connectivity=8)
        mask = np.zeros(binary_img.shape, dtype=np.uint8)

        for i in range(1, num_labels):
            x, y, bw, bh, area = stats[i]
            if area >= min_area and bw >= min_w and bh >= min_h:
                mask[labels == i] = 255

        return mask
    except Exception:
        return binary_img


def preprocess_image_for_ocr(image):
    try:
        img_array = np.array(image)

        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array

        h, w = gray.shape[:2]
        max_dim = max(h, w)
        if max_dim > 1600:
            scale = 1600 / max_dim
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

        h, w = gray.shape[:2]
        max_dim = max(h, w)
        if max_dim < 1200:
            scale = min(2.5, 1200 / max_dim)
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        gray = cv2.convertScaleAbs(gray, alpha=1.3, beta=0)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if np.mean(thresh) < 127:
            thresh = cv2.bitwise_not(thresh)

        return _remove_small_components(thresh)
    except Exception:
        return image


def ocr_image_bytes(image_data):
    try:
        image = Image.open(io.BytesIO(image_data))
        if image.mode != 'RGB':
            image = image.convert('RGB')
        processed = preprocess_image_for_ocr(image)
        text = pytesseract.image_to_string(processed, lang='spa+eng', config='--psm 6')
        return text.strip()
    except Exception:
        return ""


def ocr_images_parallel(images_data, max_workers=4):
    if not images_data:
        return []

    if len(images_data) == 1:
        return [ocr_image_bytes(images_data[0])]

    results = []
    max_workers = min(max_workers, len(images_data))

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(ocr_image_bytes, data) for data in images_data]
        for future in as_completed(futures):
            results.append(future.result())

    return results
