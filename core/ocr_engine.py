import io
import os
import sys
import logging
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2
import numpy as np
import pytesseract
from PIL import Image

# Nuevas librerías para OCR Multimodal y Corrección Difusa
try:
    import easyocr
    EASY_OCR_READER = None # Se inicializa perezosamente para ahorrar RAM
except ImportError:
    easyocr = None

try:
    from rapidfuzz import process as fuzzy_process
    from rapidfuzz import fuzz
except ImportError:
    fuzzy_process = None

# Soporte para Google Cloud Vision (deshabilitado temporalmente por falta de billing)
vision = None
GOOGLE_VISION_AVAILABLE = False

# Permitir ejecución directa del módulo
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger('CouponNotifier')

class CloudOCREngine:
    def __init__(self):
        self.client = None

    def get_text_from_image_bytes(self, image_data):
        return ""

_easyocr_lock = threading.Lock()
CLOUD_ENGINE = None

def get_cloud_engine():
    global CLOUD_ENGINE
    if CLOUD_ENGINE is None:
        CLOUD_ENGINE = CloudOCREngine()
    return CLOUD_ENGINE

def get_easyocr_reader():
    """Inicializa EasyOCR solo si es necesario (Lazy Loading)."""
    global EASY_OCR_READER
    if easyocr is None: return None
    
    with _easyocr_lock:
        if EASY_OCR_READER is None:
            logger.info("Inicializando motor de Deep Learning (EasyOCR)...")
            EASY_OCR_READER = easyocr.Reader(['es', 'en'], gpu=False) # GPU False por defecto por compatibilidad
    return EASY_OCR_READER

def _get_text_regions(img_gray):
    """
    Segmentación ROI (Region of Interest): Detecta bloques que probablemente contienen texto.
    """
    regions = []
    try:
        # Binarización para detectar bloques
        _, thresh = cv2.threshold(img_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        
        # Dilatar para unir caracteres en palabras y palabras en bloques
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5))
        dilated = cv2.dilate(thresh, kernel, iterations=2)
        
        # Encontrar contornos
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        h_img, w_img = img_gray.shape[:2]
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Filtrar por tamaño mínimo para ignorar ruido
            if w > 40 and h > 15 and w < w_img * 0.9:
                # Añadir un pequeño margen
                padx, pady = 5, 2
                x1 = max(0, x - padx)
                y1 = max(0, y - pady)
                x2 = min(w_img, x + w + padx)
                y2 = min(h_img, y + h + pady)
                regions.append((x1, y1, x2, y2))
                
        # Si no detecta nada bueno, devolver imagen completa como región única
        if not regions:
            regions.append((0, 0, w_img, h_img))
            
    except Exception as e:
        logger.error(f"Error detectando ROI: {e}")
        regions.append((0, 0, img_gray.shape[1], img_gray.shape[0]))
        
    return regions

def preprocess_image_for_ocr(image, adaptive=False):
    """
    Mejora la imagen para OCR con técnicas de Binarización Adaptativa y Morfología.
    """
    try:
        img_array = np.array(image)

        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array

        # 1. Escalado inteligente
        h, w = gray.shape[:2]
        if h < 600:
            scale = 1200 / h
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # 2. Mejora de contraste Adaptativo (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        # 3. Binarización
        if adaptive:
            # Útil para fondos con degradados o sombras
            thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                          cv2.THRESH_BINARY, 11, 2)
        else:
            # Otsu estándar
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        # 4. Asegurar texto negro sobre fondo blanco
        if np.mean(thresh) < 127:
            thresh = cv2.bitwise_not(thresh)

        # 5. Morfología: Engrosamiento de trazos
        kernel = np.ones((2, 2), np.uint8)
        thresh = cv2.erode(thresh, kernel, iterations=1)  # Erode en fondo blanco "engrosa" lo negro
        thresh = cv2.dilate(thresh, kernel, iterations=1)  # Dilatación suave para reforzar bordes

        return _remove_small_components(thresh)
    except Exception as e:
        logger.error(f"Error en pre-procesamiento OCR: {e}")
        return image

def apply_fuzzy_correction(text, known_terms):
    """
    Aplica corrección de Levensthein para corregir errores comunes de OCR.
    """
    if not fuzzy_process or not text or len(text) < 4:
        return text
    
    words = text.split()
    corrected_words = []
    
    for word in words:
        if len(word) < 4:
            corrected_words.append(word)
            continue
            
        # Buscar coincidencia cercana en términos conocidos (tiendas, palabras clave)
        match = fuzzy_process.extractOne(word.upper(), known_terms, scorer=fuzz.Ratio)
        if match and match[1] >= 85: # 85% de similitud (Distancia de Levensthein baja)
            corrected_words.append(match[0])
        else:
            corrected_words.append(word)
            
    return " ".join(corrected_words)

def ocr_image_bytes(image_data, mode="default"):
    """
    Motor OCR Multimodal con Segmentación ROI y Fallback a Deep Learning.
    mode: default | tesseract | easyocr | google
    """
    try:
        mode = (mode or "default").lower()
        base_pil = Image.open(io.BytesIO(image_data)).convert('RGB')
        img_np = np.array(base_pil)
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

        # Modo exclusivo Google Cloud (deshabilitado)
        if mode == "google":
            logger.info("☁️ OCR Google Vision deshabilitado temporalmente (sin billing)")
            return ""
        
        # 1. Segmentación ROI
        regions = _get_text_regions(gray)
        all_text = []

        # 2. Procesar cada región con el motor seleccionado
        for (x1, y1, x2, y2) in regions:
            roi = base_pil.crop((x1, y1, x2, y2))
            processed_roi = preprocess_image_for_ocr(roi)
            
            text = ""
            if mode in ("default", "tesseract"):
                text = pytesseract.image_to_string(processed_roi, lang='spa+eng', config='--psm 7').strip()
            
            # Fallback a EasyOCR solo en modo default, o uso exclusivo en modo easyocr
            if (mode == "easyocr") or (mode == "default" and (not text or len(text) < 3)):
                reader = get_easyocr_reader()
                if reader:
                    results = reader.readtext(np.array(roi), detail=0)
                    text = " ".join(results)
                    
            if text:
                all_text.append(text)
        
        final_text = " ".join(all_text)
        
        # 4. Si la segmentación falló, intento tradicional con rotación
        if len(final_text) < 5 and mode in ("default", "tesseract"):
            for angle in [0, 90, 270]:
                rot_img = base_pil.rotate(angle, expand=True)
                p_img = preprocess_image_for_ocr(rot_img, adaptive=True)
                text = pytesseract.image_to_string(p_img, lang='spa+eng', config='--psm 6').strip()
                if len(text) > len(final_text):
                    final_text = text
                if len(final_text) > 15: break

        if len(final_text) < 5 and mode == "easyocr":
            reader = get_easyocr_reader()
            if reader:
                results = reader.readtext(np.array(base_pil), detail=0)
                final_text = " ".join(results)

        # 5. ULTIMATUM: Google Cloud Vision (deshabilitado)

        return final_text.strip()
    except Exception as e:
        logger.error(f"Error crítico en motor OCR: {e}")
        return ""


def ocr_images_parallel(images_data, max_workers=4, mode="default"):
    if not images_data:
        return []

    if len(images_data) == 1:
        return [ocr_image_bytes(images_data[0], mode=mode)]

    results = []
    max_workers = min(max_workers, len(images_data))

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(ocr_image_bytes, data, mode) for data in images_data]
        for future in as_completed(futures):
            results.append(future.result())

    return results
