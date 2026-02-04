import os
import logging
from fpdf import FPDF

logger = logging.getLogger('CouponNotifier')

def generar_pdf_instrucciones(dest_path="Configuracion_OCR.pdf"):
    """
    Genera un manual PDF dinámico para la configuración de OCR y APIs.
    """
    try:
        pdf = FPDF()
        pdf.add_page()
        
        # Título
        pdf.set_font("Arial", 'B', 20)
        pdf.set_text_color(31, 83, 141) # Azul CodeNotificator
        pdf.cell(200, 20, "CodeNotificator - Manual de Configuracion", ln=True, align='C')
        
        pdf.set_font("Arial", 'B', 14)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(10)
        
        # Sección 1: Tesseract
        pdf.cell(0, 10, "1. Motor OCR Local (Tesseract)", ln=True)
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(0, 7, "Para el escaneo basico gratuito:\n"
                             "- Descargue e instale Tesseract OCR v5.0+ para Windows.\n"
                             "- Ruta requerida: C:\\Program Files\\Tesseract-OCR\\tesseract.exe\n"
                             "- IMPORTANTE: Durante la instalacion, busque la seccion 'Additional script data' "
                             "y marque 'Latin' y 'Spanish' para maxima precision.")
        pdf.ln(5)

        # Sección 2: Google Cloud
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "2. Google Cloud Vision (Modo Ultra Preciso)", ln=True)
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(0, 7, "Para procesar cupones complejos integrados en imagenes:\n"
                             "- Habilite 'Cloud Vision API' en su consola de Google Cloud.\n"
                             "- Cree una 'Cuenta de Servicio' con rol 'Usuario de Cloud Vision AI'.\n"
                             "- Descargue la llave JSON, renombrela como 'vision_key.json'.\n"
                             "- Coloque el archivo en la carpeta 'config/' o en la raiz del programa.")
        pdf.ln(5)

        # Sección 3: Estrategia Cascada
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "3. Inteligencia de Cascada (Waterfall)", ln=True)
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(0, 7, "CodeNotificator optimiza recursos siguiendo este orden:\n"
                             "1. Metadata JSON-LD: Detecta cupones oficiales de Gmail directamente.\n"
                             "2. Regex Engine: Busca patrones de texto en el cuerpo del correo.\n"
                             "3. OCR Local (Tesseract): Procesa imagenes usando su CPU.\n"
                             "4. Cloud Vision API: Solo si lo anterior falla (Requiere vision_key.json).")
        
        pdf.ln(10)
        pdf.set_font("Arial", 'I', 10)
        pdf.set_text_color(128, 128, 128)
        pdf.cell(0, 10, "Generado automaticamente por su instancia de CodeNotificator.", ln=True, align='C')

        pdf.output(dest_path)
        logger.info(f"PDF de configuracion generado en: {dest_path}")
        return True
    except Exception as e:
        logger.error(f"Error generando PDF de ayuda: {e}")
        return False

def verificar_dependencias_ocr():
    """
    Verifica si Tesseract y la llave de Google Vision están presentes.
    Retorna un diccionario con el estado de cada componente.
    """
    import shutil
    import pytesseract
    
    estado = {
        "tesseract": False,
        "vision_key": False,
        "easyocr": False,
        "mensaje_tesseract": "No encontrado",
        "mensaje_vision": "No configurado"
    }

    # 1. Verificar Tesseract
    # Intentar encontrarlo en el PATH
    if shutil.which("tesseract"):
        estado["tesseract"] = True
        estado["mensaje_tesseract"] = "Instalado (Encontrado en PATH)"
    else:
        # Verificar ruta común en Windows
        common_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(common_path):
            pytesseract.pytesseract.tesseract_cmd = common_path
            estado["tesseract"] = True
            estado["mensaje_tesseract"] = "Instalado (Ruta estándar)"
        else:
            estado["mensaje_tesseract"] = "No instalado o no está en PATH"

    # 2. Verificar vision_key.json
    possible_keys = [
        os.path.join(os.getcwd(), 'vision_key.json'),
        os.path.join(os.getcwd(), 'config', 'vision_key.json'),
        os.path.join(os.path.dirname(__file__), '..', 'vision_key.json')
    ]
    key_path = next((p for p in possible_keys if os.path.exists(p)), None)
    if key_path:
        estado["vision_key"] = True
        estado["mensaje_vision"] = "Configurado (vision_key.json)"

    # 3. Verificar EasyOCR (Opcional pero recomendado)
    try:
        import easyocr
        estado["easyocr"] = True
    except ImportError:
        estado["easyocr"] = False

    return estado
