import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import os
import sqlite3
import threading
import queue
import base64
import json
import webbrowser
import logging
import logging.handlers 
import traceback
from datetime import datetime, timedelta
import pytesseract
from PIL import Image
import io
import numpy as np
import cv2
import re
import validators
import math

# Verificar disponibilidad de bibliotecas
ML_AVAILABLE = False  # Desactivamos scikit-learn
print("Modo sin scikit-learn: usando reglas heurísticas mejoradas")

try:
    import win10toast
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    try:
        from plyer import notification
        NOTIFICATIONS_AVAILABLE = True
    except ImportError:
        NOTIFICATIONS_AVAILABLE = False
        print("Advertencia: win10toast/plyer no instalado. Las notificaciones estarán desactivadas.")
        print("Instala con: pip install win10toast")

# Consulta de búsqueda por defecto para Gmail
DEFAULT_SEARCH_QUERY = ''
DEFAULT_SEARCH_KEYWORDS = ''
DEFAULT_MAX_EMAILS = 15

# ==============================================
# CONFIGURACIÓN DE LOGGING
# ==============================================
def setup_logging():
    """Configura el sistema de logging con rotación de archivos"""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('CouponNotifier')
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'coupon_notifier.log'),
        maxBytes=5*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ==============================================
# CLASE PARA MANEJO DE BASE DE DATOS
# ==============================================
class DatabaseManager:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Tabla principal de notificaciones
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                cupon TEXT NOT NULL, 
                tienda TEXT NOT NULL,
                URL TEXT NOT NULL, 
                descuento TEXT,
                estado TEXT DEFAULT 'nuevo',
                usuario_valido INTEGER DEFAULT 0,
                es_valido INTEGER DEFAULT 1,
                confianza REAL DEFAULT 0.5,
                Fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabla de configuración
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                intervalo_minutos INTEGER DEFAULT 30,
                client_id TEXT,
                client_secret TEXT,
                refresh_token TEXT,
                ultimo_escaneo DATETIME,
                notificaciones_activas INTEGER DEFAULT 1,
                aprendizaje_activo INTEGER DEFAULT 1,
                CHECK (id = 1)
            )
        ''')
        
        cursor.execute('''
            INSERT OR IGNORE INTO config (id, intervalo_minutos) 
            VALUES (1, 30)
        ''')
        
        # Verificar y agregar columnas si no existen
        cursor.execute("PRAGMA table_info(config)")
        cols = [row[1] for row in cursor.fetchall()]
        
        columnas_a_agregar = [
            ('search_query', 'TEXT'),
            ('search_keywords', 'TEXT'),
            ('max_emails', 'INTEGER')
        ]
        
        for col_name, col_type in columnas_a_agregar:
            if col_name not in cols:
                cursor.execute(f"ALTER TABLE config ADD COLUMN {col_name} {col_type}")
        
        # Valores por defecto
        cursor.execute(
            "UPDATE config SET search_query = ? WHERE id = 1 AND (search_query IS NULL OR search_query = '')",
            (DEFAULT_SEARCH_QUERY,)
        )
        cursor.execute(
            "UPDATE config SET search_keywords = ? WHERE id = 1 AND (search_keywords IS NULL OR search_keywords = '')",
            (DEFAULT_SEARCH_KEYWORDS,)
        )
        cursor.execute(
            "UPDATE config SET max_emails = ? WHERE id = 1 AND (max_emails IS NULL OR max_emails < 1)",
            (DEFAULT_MAX_EMAILS,)
        )
        
        # Tabla de aprendizaje simplificada (sin ML)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learning_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tienda TEXT,
                patron TEXT,
                total_apariciones INTEGER DEFAULT 0,
                exitosos INTEGER DEFAULT 0,
                confianza REAL DEFAULT 0.0,
                ultimo_uso DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabla de feedback del usuario
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cupon_id INTEGER,
                cupon_text TEXT,
                tienda TEXT,
                es_valido BOOLEAN,
                comentario TEXT,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cupon_id) REFERENCES notifications(id)
            )
        ''')
        
        self.conn.commit()
        logger.info("Tablas de base de datos creadas/verificadas")
    
    def get_config(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM config WHERE id = 1")
        return cursor.fetchone()
    
    def update_config(self, **kwargs):
        if not kwargs:
            return
        
        set_clause = []
        values = []
        
        for key, value in kwargs.items():
            if value is not None:
                set_clause.append(f"{key} = ?")
                values.append(value)
        
        if set_clause:
            query = f"UPDATE config SET {', '.join(set_clause)} WHERE id = 1"
            cursor = self.conn.cursor()
            cursor.execute(query, values)
            self.conn.commit()
            logger.info(f"Configuración actualizada: {', '.join(kwargs.keys())}")
    
    def update_last_scan(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE config SET ultimo_escaneo = CURRENT_TIMESTAMP WHERE id = 1"
        )
        self.conn.commit()
    
    def add_notification(self, cupon, tienda, url, descuento=None, confianza=0.5):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO notifications (cupon, tienda, URL, descuento, confianza) VALUES (?, ?, ?, ?, ?)",
            (cupon, tienda, url, descuento, confianza)
        )
        self.conn.commit()
        logger.info(f"Cupón agregado: {cupon} - {tienda} (confianza: {confianza:.2f})")
        return cursor.lastrowid
    
    def notification_exists(self, cupon):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM notifications WHERE cupon = ? LIMIT 1",
            (cupon,)
        )
        return cursor.fetchone() is not None
    
    def get_notifications(self, limit=100, estado=None):
        query = "SELECT id, cupon, tienda, URL, descuento, estado, usuario_valido, es_valido, confianza, Fecha FROM notifications"
        params = []
        
        if estado:
            query += " WHERE estado = ?"
            params.append(estado)
        
        query += " ORDER BY confianza DESC, Fecha DESC LIMIT ?"
        params.append(limit)
        
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()
    
    def get_search_query(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT search_query FROM config WHERE id = 1")
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
        return DEFAULT_SEARCH_QUERY
    
    def get_max_emails(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT max_emails FROM config WHERE id = 1")
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return max(int(row[0]), 1)
            except:
                return DEFAULT_MAX_EMAILS
        return DEFAULT_MAX_EMAILS
    
    def update_cupon_validity(self, cupon_id, es_valido, confianza=1.0):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE notifications SET es_valido = ?, usuario_valido = 1, confianza = ? WHERE id = ?",
            (1 if es_valido else 0, confianza, cupon_id)
        )
        self.conn.commit()
        logger.info(f"Cupón {cupon_id} marcado como {'válido' if es_valido else 'inválido'}")
    
    def add_user_feedback(self, cupon_id, cupon_text, tienda, es_valido, comentario=""):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO user_feedback (cupon_id, cupon_text, tienda, es_valido, comentario)
            VALUES (?, ?, ?, ?, ?)
        ''', (cupon_id, cupon_text, tienda, 1 if es_valido else 0, comentario))
        self.conn.commit()
        return cursor.lastrowid
    
    def update_learning_pattern(self, tienda, patron, es_valido):
        cursor = self.conn.cursor()
        
        # Buscar patrón existente
        cursor.execute('''
            SELECT id, total_apariciones, exitosos 
            FROM learning_patterns 
            WHERE tienda = ? AND patron = ?
        ''', (tienda, patron))
        
        row = cursor.fetchone()
        
        if row:
            # Actualizar existente
            pattern_id, total, exitosos = row
            total += 1
            exitosos += 1 if es_valido else 0
            confianza = exitosos / total if total > 0 else 0.0
            
            cursor.execute('''
                UPDATE learning_patterns 
                SET total_apariciones = ?, exitosos = ?, confianza = ?, ultimo_uso = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (total, exitosos, confianza, pattern_id))
        else:
            # Insertar nuevo
            confianza = 1.0 if es_valido else 0.0
            exitosos = 1 if es_valido else 0
            
            cursor.execute('''
                INSERT INTO learning_patterns (tienda, patron, total_apariciones, exitosos, confianza)
                VALUES (?, ?, 1, ?, ?)
            ''', (tienda, patron, exitosos, confianza))
        
        self.conn.commit()
    
    def get_pattern_confidence(self, tienda, patron):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT confianza FROM learning_patterns 
            WHERE tienda = ? AND patron = ?
        ''', (tienda, patron))
        
        row = cursor.fetchone()
        return row[0] if row else 0.5
    
    def get_learning_stats(self):
        cursor = self.conn.cursor()
        
        # Estadísticas de feedback
        cursor.execute('''
            SELECT 
                COUNT(*) as total_feedback,
                SUM(CASE WHEN es_valido = 1 THEN 1 ELSE 0 END) as validos,
                COUNT(DISTINCT tienda) as tiendas_aprendidas
            FROM user_feedback
        ''')
        feedback_stats = cursor.fetchone()
        
        # Estadísticas de patrones
        cursor.execute('''
            SELECT 
                COUNT(*) as total_patrones,
                AVG(confianza) as confianza_promedio
            FROM learning_patterns
        ''')
        pattern_stats = cursor.fetchone()
        
        return feedback_stats, pattern_stats
    
    def get_top_patterns(self, limit=10):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT tienda, patron, confianza, total_apariciones
            FROM learning_patterns 
            WHERE total_apariciones > 0
            ORDER BY confianza DESC, total_apariciones DESC
            LIMIT ?
        ''', (limit,))
        return cursor.fetchall()
    
    def close(self):
        self.conn.close()
        logger.info("Conexión a base de datos cerrada")

# ==============================================
# SISTEMA DE APRENDIZAJE SIMPLIFICADO (SIN scikit-learn)
# ==============================================
class SimpleLearningSystem:
    def __init__(self, db_manager):
        self.db = db_manager
        self.pattern_cache = {}
        
    def extract_pattern(self, texto_cupon):
        """Extrae un patrón simplificado del código del cupón"""
        texto = texto_cupon.upper().strip()
        
        # Detectar tipo de patrón
        if '-' in texto:
            parts = texto.split('-')
            if len(parts) == 2:
                return "XXXX-XXXX"
            elif len(parts) == 3:
                return "XXXX-XXXX-XXXX"
            else:
                return "MULTI-HYPHEN"
        
        # Verificar patrones comunes
        if re.match(r'^[A-Z]{2}\d{6}$', texto):
            return "LLDDDDDD"  # 2 letras + 6 dígitos
        elif re.match(r'^\d{4}[A-Z]{3}$', texto):
            return "DDDDLLL"   # 4 dígitos + 3 letras
        elif re.match(r'^[A-Z]{3}\d{5}$', texto):
            return "LLLDDDDD"  # 3 letras + 5 dígitos
        elif re.match(r'^[A-Z]{4}\d{4}$', texto):
            return "LLLLDDDD"  # 4 letras + 4 dígitos
        elif re.match(r'^\d{8}$', texto):
            return "DDDDDDDD"  # 8 dígitos
        elif re.match(r'^[A-Z]{8}$', texto):
            return "LLLLLLLL"  # 8 letras
        elif re.match(r'^[A-Z0-9]{8}$', texto):
            return "ALPHANUM8"  # 8 caracteres alfanuméricos
        elif re.match(r'^[A-Z0-9]{10}$', texto):
            return "ALPHANUM10" # 10 caracteres alfanuméricos
        
        # Patrón por longitud y composición
        length = len(texto)
        digit_count = sum(1 for c in texto if c.isdigit())
        letter_count = sum(1 for c in texto if c.isalpha())
        
        if digit_count == 0:
            return f"ALL_LETTERS_{length}"
        elif letter_count == 0:
            return f"ALL_DIGITS_{length}"
        else:
            ratio = digit_count / max(letter_count, 1)
            if ratio > 2:
                return f"MOSTLY_DIGITS_{length}"
            elif ratio < 0.5:
                return f"MOSTLY_LETTERS_{length}"
            else:
                return f"MIXED_{length}"
    
    def calculate_confidence(self, texto_cupon, tienda=None):
        """Calcula confianza basada en reglas heurísticas y aprendizaje previo"""
        texto = texto_cupon.upper().strip()
        
        # Reglas básicas de validez
        if len(texto) < 4 or len(texto) > 25:
            return 0.1  # Muy baja confianza
        
        has_digit = any(c.isdigit() for c in texto)
        has_letter = any(c.isalpha() for c in texto)
        
        if not (has_digit and has_letter):
            return 0.2  # Baja confianza
        
        # Patrones comunes de cupones válidos
        common_valid_patterns = [
            r'^[A-Z0-9]{4,}$',
            r'^[A-Z0-9]{4,}-[A-Z0-9]{4,}$',
            r'^[A-Z]{2,}\d{3,}[A-Z]{0,3}$',
            r'^\d{4,}[A-Z]{2,}$',
            r'^[A-Z0-9]{8,12}$',
        ]
        
        for pattern in common_valid_patterns:
            if re.match(pattern, texto):
                base_confidence = 0.7
                break
        else:
            base_confidence = 0.4
        
        # Ajustar por tienda específica si tenemos datos
        if tienda:
            patron = self.extract_pattern(texto)
            learned_confidence = self.db.get_pattern_confidence(tienda, patron)
            
            # Combinar confianza aprendida con reglas heurísticas
            if learned_confidence > 0:
                # Ponderar: 70% aprendizaje, 30% reglas heurísticas
                final_confidence = (learned_confidence * 0.7) + (base_confidence * 0.3)
                return min(max(final_confidence, 0.1), 0.95)
        
        return base_confidence
    
    def learn_from_feedback(self, cupon_text, tienda, es_valido):
        """Aprende del feedback del usuario"""
        patron = self.extract_pattern(cupon_text)
        
        # Actualizar en base de datos
        self.db.update_learning_pattern(tienda, patron, es_valido)
        
        # Actualizar caché
        cache_key = f"{tienda}_{patron}"
        if cache_key not in self.pattern_cache:
            self.pattern_cache[cache_key] = {'total': 0, 'success': 0}
        
        self.pattern_cache[cache_key]['total'] += 1
        if es_valido:
            self.pattern_cache[cache_key]['success'] += 1
        
        logger.info(f"Aprendizaje: {cupon_text} -> {patron} ({'válido' if es_valido else 'inválido'})")
    
    def get_stats(self):
        """Obtiene estadísticas del sistema de aprendizaje"""
        feedback_stats, pattern_stats = self.db.get_learning_stats()
        
        return {
            'total_feedback': feedback_stats[0] if feedback_stats else 0,
            'valid_feedback': feedback_stats[1] if feedback_stats else 0,
            'stores_learned': feedback_stats[2] if feedback_stats else 0,
            'total_patterns': pattern_stats[0] if pattern_stats else 0,
            'avg_confidence': pattern_stats[1] if pattern_stats else 0.0,
        }

# ==============================================
# SISTEMA DE NOTIFICACIONES
# ==============================================
class SystemNotifier:
    def __init__(self, enabled=True):
        self.enabled = enabled and NOTIFICATIONS_AVAILABLE
        self.last_notification = None
        self.min_interval = timedelta(seconds=30)
    
    def show_notification(self, title, message, duration=5):
        if not self.enabled:
            return
        
        now = datetime.now()
        if self.last_notification and (now - self.last_notification) < self.min_interval:
            return
        
        try:
            if 'win10toast' in globals():
                toast = win10toast.ToastNotifier()
                toast.show_toast(title, message, duration=duration, threaded=True)
            elif 'notification' in globals():
                notification.notify(title=title, message=message, timeout=duration)
            
            self.last_notification = now
            logger.info(f"Notificación: {title}")
            
        except Exception as e:
            logger.error(f"Error en notificación: {e}")
            print(f"🔔 {title}: {message}")
    
    def notify_new_coupons(self, count, coupons_list):
        if count == 0 or not self.enabled:
            return
        
        title = "🎉 Nuevos Cupones"
        
        if count == 1:
            cupon = coupons_list[0]
            message = f"{cupon['codigo']} - {cupon['tienda']}"
            if cupon.get('descuento'):
                message += f" ({cupon['descuento']})"
        elif count <= 3:
            cupones_str = ", ".join([c['codigo'] for c in coupons_list[:3]])
            message = f"{count} nuevos: {cupones_str}"
        else:
            message = f"¡{count} cupones nuevos!"
        
        self.show_notification(title, message)
    
    def notify_scan_complete(self, total_found, new_count):
        if not self.enabled:
            return
        
        title = "🔍 Escaneo Completado"
        message = f"{new_count} nuevos ({total_found} total)" if new_count > 0 else f"{total_found} en total"
        
        self.show_notification(title, message)
    
    def enable(self):
        self.enabled = True and NOTIFICATIONS_AVAILABLE
    
    def disable(self):
        self.enabled = False

# ==============================================
# AUTENTICACIÓN GMAIL
# ==============================================
class GmailAuthenticator:
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
    
    @staticmethod
    def obtener_tokens_interactivo():
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            import tkinter as tk
            from tkinter import filedialog
            
            root = tk.Tk()
            root.withdraw()
            
            file_path = filedialog.askopenfilename(
                title="Selecciona tu archivo credentials.json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
            )
            
            root.destroy()
            
            if not file_path:
                raise ValueError("No se seleccionó archivo credentials.json")
            
            with open(file_path, 'r', encoding='utf-8') as f:
                credenciales = json.load(f)
            
            with open('temp_creds.json', 'w') as f:
                json.dump(credenciales, f)
            
            flow = InstalledAppFlow.from_client_secrets_file(
                'temp_creds.json',
                GmailAuthenticator.SCOPES
            )
            
            creds = flow.run_local_server(port=8080, prompt='consent')
            
            container = credenciales.get('installed') or credenciales.get('web')
            if not container:
                raise ValueError('El credentials.json no contiene las claves esperadas.')
            
            client_id = container.get('client_id')
            client_secret = container.get('client_secret')
            
            if not client_id or not client_secret:
                raise ValueError('Faltan client_id o client_secret en credentials.json.')
            
            logger.info("Tokens obtenidos exitosamente")
            return {
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': creds.refresh_token
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo tokens: {e}")
            raise
        finally:
            if os.path.exists('temp_creds.json'):
                os.remove('temp_creds.json')
    
    @staticmethod
    def get_credentials(db_manager):
        from google.oauth2.credentials import Credentials
        config = db_manager.get_config()
        if not config:
            return None
        
        client_id = config[2]
        client_secret = config[3]
        refresh_token = config[4]
        
        if not all([client_id, client_secret, refresh_token]):
            return None
        
        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=client_id,
            client_secret=client_secret,
            scopes=GmailAuthenticator.SCOPES
        )

# ==============================================
# PROCESADOR GMAIL + OCR CON APRENDIZAJE SIMPLIFICADO
# ==============================================
class GmailOCRProcessor:
    def __init__(self, db_manager, learning_system=None):
        self.db = db_manager
        self.learning_system = learning_system
        self.service = None
        self.credentials = None
        self.last_auth = None
        self.auth_timeout = 3500
        self.current_email = None
        self.is_authenticated = False
    
    def authenticate(self, force_refresh=False):
        try:
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            
            if (not force_refresh and self.is_authenticated and self.last_auth and 
                (datetime.now() - self.last_auth).seconds < self.auth_timeout):
                return True, "Autenticación en cache"
            
            creds = GmailAuthenticator.get_credentials(self.db)
            if not creds:
                return False, "Credenciales no configuradas"
            
            if creds.expired or force_refresh:
                creds.refresh(Request())
            
            self.service = build('gmail', 'v1', credentials=creds)
            self.credentials = creds
            self.is_authenticated = True
            self.last_auth = datetime.now()

            try:
                profile = self.service.users().getProfile(userId='me').execute()
                self.current_email = profile.get('emailAddress')
                logger.info(f"Autenticado como: {self.current_email}")
            except:
                self.current_email = None

            return True, "Autenticación exitosa"
            
        except Exception as e:
            logger.error(f"Error de autenticación: {e}")
            self.is_authenticated = False
            return False, f"Error de autenticación: {str(e)}"
    
    def search_emails(self, query=None, max_results=20):
        try:
            if not self.service:
                success, _ = self.authenticate()
                if not success:
                    return []

            params = {'userId': 'me', 'maxResults': max_results}
            if query and str(query).strip():
                params['q'] = str(query).strip()

            results = self.service.users().messages().list(**params).execute()
            messages = results.get('messages', [])
            logger.info(f"Encontrados {len(messages)} correos")
            return messages

        except Exception as e:
            logger.error(f"Error buscando correos: {e}")
            return []
    
    def extract_text(self, image_data):
        try:
            image = Image.open(io.BytesIO(image_data))
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Procesamiento simple de imagen
            img_array = np.array(image)
            if len(img_array.shape) == 3:
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            else:
                gray = img_array
            
            # Aplicar OCR
            text = pytesseract.image_to_string(gray, lang='spa+eng', config='--psm 6')
            return text.strip()
            
        except Exception as e:
            logger.error(f"Error en OCR: {e}")
            return ""
    
    def extract_store_name_from_sender(self, from_header):
        """
        Extrae el nombre de la tienda del remitente del correo.
        Ejemplo: "The M Jewelers <contact@themjewelersny.com>" -> "The M Jewelers"
        """
        if not from_header:
            return "Desconocida"
        
        try:
            # Patrón 1: Nombre <email@dominio.com>
            match = re.match(r'^"?([^"<]+)"?\s*<[^>]+>$', from_header)
            if match:
                store_name = match.group(1).strip()
                # Limpiar comillas adicionales
                store_name = store_name.strip('"\'')
                if store_name:
                    return store_name
            
            # Patrón 2: Solo email - extraer del dominio
            email_match = re.search(r'<([^>]+)>', from_header)
            if email_match:
                email = email_match.group(1)
                # Intentar extraer del dominio
                domain_match = re.search(r'@([^.]+)', email)
                if domain_match:
                    domain_part = domain_match.group(1)
                    # Convertir a formato legible (ej: themjewelersny -> The M Jewelers NY)
                    store_name = domain_part.replace('themjewelersny', 'The M Jewelers')
                    store_name = ' '.join(word.capitalize() for word in re.split(r'[^a-zA-Z0-9]+', store_name))
                    return store_name
            
            # Si no coincide ningún patrón, usar el header completo
            if from_header and '@' in from_header:
                # Extraer la parte antes del <
                name_part = from_header.split('<')[0].strip()
                if name_part:
                    return name_part.strip('"\'')
            
            return "Desconocida"
            
        except Exception as e:
            logger.error(f"Error extrayendo nombre del remitente '{from_header}': {e}")
            return "Desconocida"
    
    def extract_coupon_info(self, text, tienda_from_sender=None):
        info = {
            'codigo': '',
            'tienda': 'Desconocida',
            'url': '',
            'descuento': '',
            'contexto': text[:300]
        }
        
        # Si tenemos tienda del remitente, usarla como valor predeterminado
        if tienda_from_sender and tienda_from_sender != 'Desconocida':
            info['tienda'] = tienda_from_sender
        
        # Buscar descuento
        desc_matches = re.findall(r'(\d{1,3}(?:[,.]\d{1,2})?)%', text)
        if desc_matches:
            info['descuento'] = f"{max(desc_matches, key=lambda x: float(x.replace(',', '.')))}%"
        
        # Buscar códigos
        patterns = [
            r'(?:codigo|código|cup[oó]n|promo|coupon|code)[:\s\-]*([A-Z0-9\-]{6,20})',
            r'([A-Z0-9]{4,}[-\s]?[A-Z0-9]{4,}[-\s]?[A-Z0-9]{4,})',
            r'CODE\s*[:=]?\s*([A-Z0-9\-]+)',
            r'([A-Z]{2,}\d{3,}[A-Z]{0,3})',
            r'(\d{4,}[A-Z]{2,})',
        ]
        
        found_codes = []
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                code = match.group(1).upper().strip()
                if self.is_valid_coupon_code(code, info['tienda']):
                    found_codes.append(code)
        
        if found_codes:
            # Seleccionar el código más largo o el que tenga contexto de "CODE"
            for code in found_codes:
                context_start = max(0, text.upper().find(code) - 20)
                context_end = min(len(text), text.upper().find(code) + len(code) + 20)
                context = text[context_start:context_end].upper()
                if any(keyword in context for keyword in ['CODE', 'CODIGO', 'CUPON']):
                    info['codigo'] = code
                    break
            else:
                info['codigo'] = max(found_codes, key=len)
        
        # Buscar URL
        url_match = re.search(r'https?://(?:www\.)?[\w\-]+(?:\.[\w\-]+)+[/\w\-\.]*', text)
        if url_match:
            info['url'] = url_match.group(0)
        
        # Solo identificar tienda desde el texto si no tenemos del remitente
        if info['tienda'] == 'Desconocida':
            tiendas = {
                'amazon': ['amazon', 'amzn'],
                'mercado libre': ['mercado libre', 'mercadolibre', 'ml'],
                'ebay': ['ebay'],
                'aliexpress': ['aliexpress'],
                'walmart': ['walmart'],
            }
            
            text_lower = text.lower()
            for tienda, keywords in tiendas.items():
                if any(keyword in text_lower for keyword in keywords):
                    info['tienda'] = tienda.title()
                    break
        
        return info
    
    def is_valid_coupon_code(self, code, tienda=None):
        """Verifica si un código es probablemente un cupón válido"""
        if not code or len(code) < 4 or len(code) > 25:
            return False
        
        # Usar sistema de aprendizaje si está disponible
        if self.learning_system:
            confidence = self.learning_system.calculate_confidence(code, tienda)
            return confidence > 0.5  # Umbral del 50%
        
        # Reglas heurísticas básicas
        has_digit = any(c.isdigit() for c in code)
        has_letter = any(c.isalpha() for c in code)
        
        if not (has_digit and has_letter):
            return False
        
        # Verificar patrones comunes
        patterns = [
            r'^[A-Z0-9]{4,}$',
            r'^[A-Z0-9]{4,}-[A-Z0-9]{4,}$',
            r'^[A-Z]{2,}\d{3,}[A-Z]{0,3}$',
            r'^\d{4,}[A-Z]{2,}$',
        ]
        
        return any(re.match(pattern, code, re.IGNORECASE) for pattern in patterns)
    
    def process_email(self, message_id):
        try:
            message = self.service.users().messages().get(
                userId='me', id=message_id, format='full').execute()
            
            headers = message.get('payload', {}).get('headers', [])
            
            # Obtener asunto
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sin asunto')
            
            # Obtener remitente para identificar tienda
            from_header = next((h['value'] for h in headers if h['name'] == 'From'), '')
            tienda_from_email = self.extract_store_name_from_sender(from_header)
            
            logger.info(f"Procesando: {subject[:50]}... (Remitente: {from_header})")
            coupons_found = []
            
            # Extraer texto del cuerpo del correo
            def extract_body(part):
                if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                    data = part['body']['data']
                    return base64.urlsafe_b64decode(data.encode('UTF-8')).decode('utf-8', errors='ignore')
                
                if 'parts' in part:
                    for subpart in part['parts']:
                        text = extract_body(subpart)
                        if text:
                            return text
                return ""
            
            body_text = extract_body(message.get('payload', {}))
            
            if body_text:
                coupon_info = self.extract_coupon_info(body_text, tienda_from_email)
                
                # Si encontramos un cupón, usar el nombre de la tienda del remitente
                if coupon_info['codigo']:
                    # Priorizar el nombre extraído del remitente
                    if tienda_from_email and tienda_from_email != 'Desconocida':
                        coupon_info['tienda'] = tienda_from_email
                    
                    # Calcular confianza
                    confidence = 0.7  # Valor por defecto
                    if self.learning_system:
                        confidence = self.learning_system.calculate_confidence(
                            coupon_info['codigo'], coupon_info['tienda']
                        )
                    
                    coupon_info['confianza'] = confidence
                    coupon_info['remitente'] = from_header  # Guardar el remitente completo
                    coupons_found.append(coupon_info)
                    logger.info(f"Cupón encontrado: {coupon_info['codigo']} - Tienda: {coupon_info['tienda']} (confianza: {confidence:.2f})")
            
            return coupons_found
            
        except Exception as e:
            logger.error(f"Error procesando email: {e}")
            return []
    
    def scan_emails(self, max_emails=None):
        logger.info("Iniciando escaneo de correos...")
        success, message = self.authenticate()
        if not success:
            return [], message
        
        try:
            if max_emails is None:
                max_emails = self.db.get_max_emails()
            
            query = self.db.get_search_query()
            emails = self.search_emails(query=query, max_results=max_emails)
            all_coupons = []
            
            for i, email in enumerate(emails, 1):
                coupons = self.process_email(email['id'])
                for coupon in coupons:
                    if not self.db.notification_exists(coupon['codigo']):
                        # Guardar con confianza calculada
                        self.db.add_notification(
                            coupon['codigo'],
                            coupon['tienda'],
                            coupon['url'],
                            coupon.get('descuento'),
                            coupon.get('confianza', 0.5)
                        )
                        all_coupons.append(coupon)
            
            self.db.update_last_scan()
            logger.info(f"Escaneo completado. Encontrados {len(all_coupons)} cupones nuevos")
            
            return all_coupons, f"Encontrados {len(all_coupons)} cupones nuevos"
            
        except Exception as e:
            logger.error(f"Error en escaneo: {traceback.format_exc()}")
            return [], f"Error: {str(e)[:100]}"

# ==============================================
# INTERFAZ GRÁFICA PRINCIPAL
# ==============================================
class CouponNotifierApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Notificador de Cupones Inteligente")
        self.root.geometry("1000x700")
        
        try:
            self.root.iconbitmap('icon.ico')
        except:
            pass
        
        db_path = os.path.join(os.path.dirname(__file__), 'notifications.db')
        self.db = DatabaseManager(db_path)
        self.learning_system = SimpleLearningSystem(self.db)
        self.processor = GmailOCRProcessor(self.db, self.learning_system)
        self.notifier = SystemNotifier()
        
        self.queue = queue.Queue()
        self.scanning = False
        self.auto_scan_id = None
        self.selected_cupon_id = None
        
        self.setup_ui()
        self.load_notifications()
        self.check_queue()
        self.check_configuration()
        
        logger.info("Aplicación iniciada correctamente")
    
    def setup_ui(self):
        # Configuración de estilos
        COLORS = {
            'primary': '#2C3E50',
            'secondary': '#3498DB',
            'accent': '#E74C3C',
            'success': '#27AE60',
            'warning': '#F39C12',
            'light': '#ECF0F1',
            'dark': '#2C3E50',
        }
        
        # Frame principal
        main_frame = tk.Frame(self.root, bg=COLORS['light'])
        main_frame.pack(fill="both", expand=True)
        
        # ========== BARRA SUPERIOR ==========
        top_frame = tk.Frame(main_frame, bg=COLORS['primary'], height=70)
        top_frame.pack(fill="x", pady=(0, 5))
        
        tk.Label(top_frame, text="🤖 NOTIFICADOR DE CUPONES INTELIGENTE", 
                font=("Arial", 16, "bold"), bg=COLORS['primary'], fg="white").pack(pady=15)
        
        # ========== BARRA DE HERRAMIENTAS ==========
        toolbar = tk.Frame(main_frame, bg=COLORS['light'])
        toolbar.pack(fill="x", padx=10, pady=5)
        
        buttons = [
            ("⚙️ Configuración", self.open_config, COLORS['secondary']),
            ("🔍 Escanear Ahora", self.start_scan, COLORS['success']),
            ("📊 Estadísticas", self.show_stats, COLORS['warning']),
            ("📋 Copiar", self.copy_selection, COLORS['secondary']),
            ("🗑️ Limpiar", self.clear_all, COLORS['accent']),
        ]
        
        for text, command, color in buttons:
            btn = tk.Button(toolbar, text=text, command=command,
                           bg=color, fg="white", font=("Arial", 10),
                           relief="flat", padx=15, pady=5)
            btn.pack(side="left", padx=2)
        
        # ========== BARRA DE PROGRESO ==========
        self.progress_frame = tk.Frame(main_frame, bg=COLORS['light'])
        self.progress_frame.pack(fill="x", padx=10, pady=5)
        
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode='indeterminate')
        self.progress_bar.pack(fill="x")
        self.progress_frame.pack_forget()  # Ocultar inicialmente
        
        # ========== BARRA DE FEEDBACK ==========
        feedback_frame = tk.Frame(main_frame, bg='#D5F4E6', height=40)
        feedback_frame.pack(fill="x", padx=10, pady=5)
        
        tk.Label(feedback_frame, text="¿Este cupón funcionó?", 
                font=("Arial", 10, "bold"), bg='#D5F4E6').pack(side="left", padx=(10, 15))
        
        self.valid_btn = tk.Button(feedback_frame, text="✅ Sí", command=self.mark_as_valid,
                                  bg='#27AE60', fg='white', state="disabled",
                                  font=("Arial", 9), relief="flat", padx=15)
        self.valid_btn.pack(side="left", padx=2)
        
        self.invalid_btn = tk.Button(feedback_frame, text="❌ No", command=self.mark_as_invalid,
                                    bg='#E74C3C', fg='white', state="disabled",
                                    font=("Arial", 9), relief="flat", padx=15)
        self.invalid_btn.pack(side="left", padx=2)
        
        # ========== LISTA DE CUPONES ==========
        list_frame = tk.Frame(main_frame, bg=COLORS['light'])
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Treeview
        columns = ("ID", "Cupón", "Tienda", "Descuento", "URL", "Confianza", "Fecha")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=15)
        
        # Configurar columnas
        col_widths = [0, 150, 120, 80, 200, 80, 120]  # ID oculto
        for col, width in zip(columns, col_widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="center")
        
        # Scrollbars
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)
        
        # ========== BARRA INFERIOR ==========
        bottom_frame = tk.Frame(main_frame, bg=COLORS['dark'])
        bottom_frame.pack(fill="x", side="bottom", pady=(5, 0))
        
        # Contador
        self.count_label = tk.Label(bottom_frame, text="0 cupones", 
                                   font=("Arial", 10, "bold"), bg=COLORS['dark'], fg="white")
        self.count_label.pack(side="left", padx=20, pady=8)
        
        # Estadísticas ML
        self.stats_label = tk.Label(bottom_frame, text="ML: 0 ejemplos", 
                                   font=("Arial", 9), bg=COLORS['dark'], fg="#BDC3C7")
        self.stats_label.pack(side="left", padx=20, pady=8)
        
        # Eventos
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind("<Double-Button-1>", self.show_details)
    
    def on_tree_select(self, event):
        selection = self.tree.selection()
        if selection:
            item = self.tree.item(selection[0])
            self.selected_cupon_id = item['values'][0]
            self.valid_btn.config(state="normal")
            self.invalid_btn.config(state="normal")
        else:
            self.selected_cupon_id = None
            self.valid_btn.config(state="disabled")
            self.invalid_btn.config(state="disabled")
    
    def mark_as_valid(self):
        if not self.selected_cupon_id:
            return
        
        # Obtener datos del cupón
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT cupon, tienda FROM notifications WHERE id = ?', (self.selected_cupon_id,))
        cupon_data = cursor.fetchone()
        
        if cupon_data:
            cupon_text, tienda = cupon_data
            
            # Aprender del feedback
            self.learning_system.learn_from_feedback(cupon_text, tienda, True)
            
            # Actualizar base de datos
            self.db.update_cupon_validity(self.selected_cupon_id, True, 1.0)
            self.db.add_user_feedback(self.selected_cupon_id, cupon_text, tienda, True, "Marcado como válido")
            
            # Actualizar UI
            self.load_notifications()
            self.update_stats_display()
            
            # Notificación
            self.notifier.show_notification("✅ Feedback Registrado", 
                                          f"'{cupon_text}' marcado como válido")
            
            messagebox.showinfo("Éxito", "¡Gracias por tu feedback! El sistema ha aprendido de este ejemplo.")
    
    def mark_as_invalid(self):
        if not self.selected_cupon_id:
            return
        
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT cupon, tienda FROM notifications WHERE id = ?', (self.selected_cupon_id,))
        cupon_data = cursor.fetchone()
        
        if cupon_data:
            cupon_text, tienda = cupon_data
            
            # Aprender del feedback
            self.learning_system.learn_from_feedback(cupon_text, tienda, False)
            
            # Actualizar base de datos
            self.db.update_cupon_validity(self.selected_cupon_id, False, 0.0)
            self.db.add_user_feedback(self.selected_cupon_id, cupon_text, tienda, False, "Marcado como inválido")
            
            # Actualizar UI
            self.load_notifications()
            self.update_stats_display()
            
            # Notificación
            self.notifier.show_notification("❌ Feedback Registrado", 
                                          f"'{cupon_text}' marcado como inválido")
            
            messagebox.showinfo("Éxito", "Feedback registrado. El sistema mejorará sus detecciones.")
    
    def update_stats_display(self):
        stats = self.learning_system.get_stats()
        self.stats_label.config(text=f"ML: {stats['total_feedback']} ejemplos")
    
    def load_notifications(self):
        # Limpiar treeview
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Cargar datos
        notifications = self.db.get_notifications(limit=50)
        
        for notif in notifications:
            cupon_id, cupon, tienda, url, descuento, estado, usuario_valido, es_valido, confianza, fecha = notif
            
            # Formatear fecha
            if isinstance(fecha, str):
                try:
                    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d %H:%M:%S")
                    fecha_str = fecha_dt.strftime("%d/%m %H:%M")
                except:
                    fecha_str = fecha
            else:
                fecha_str = str(fecha)
            
            # Formatear confianza
            conf_str = f"{confianza:.0%}"
            
            # Insertar en treeview
            self.tree.insert("", "end", values=(
                cupon_id, cupon, tienda, descuento or "", url, conf_str, fecha_str
            ))
        
        # Actualizar contador
        count = len(notifications)
        self.count_label.config(text=f"{count} cupones encontrados")
        
        # Actualizar estadísticas
        self.update_stats_display()
    
    def show_stats(self):
        stats = self.learning_system.get_stats()
        patterns = self.db.get_top_patterns(5)
        
        stats_window = tk.Toplevel(self.root)
        stats_window.title("📊 Estadísticas del Sistema")
        stats_window.geometry("500x400")
        
        # Mostrar estadísticas
        info_text = f"""
        📈 **Estadísticas de Aprendizaje:**
        
        Total de feedback: {stats['total_feedback']}
        Feedback válido: {stats['valid_feedback']}
        Tiendas aprendidas: {stats['stores_learned']}
        Patrones almacenados: {stats['total_patterns']}
        Confianza promedio: {stats['avg_confidence']:.1%}
        
        🔍 **Patrones más confiables:**
        """
        
        text_widget = scrolledtext.ScrolledText(stats_window, wrap=tk.WORD, font=("Arial", 10))
        text_widget.pack(fill="both", expand=True, padx=10, pady=10)
        text_widget.insert("1.0", info_text)
        
        if patterns:
            for tienda, patron, confianza, total in patterns:
                text_widget.insert(tk.END, 
                    f"\n• {tienda}: {patron} ({confianza:.0%} en {total} ejemplos)")
        else:
            text_widget.insert(tk.END, "\n\nAún no hay patrones aprendidos.")
        
        text_widget.config(state="disabled")
        
        tk.Button(stats_window, text="Cerrar", command=stats_window.destroy).pack(pady=10)
    
    def start_scan(self):
        if self.scanning:
            messagebox.showwarning("Escaneo en curso", "Ya hay un escaneo en progreso")
            return
        
        self.scanning = True
        self.progress_frame.pack(fill="x", padx=10, pady=5)
        self.progress_bar.start()
        
        thread = threading.Thread(target=self.perform_scan, daemon=True)
        thread.start()
    
    def perform_scan(self):
        try:
            coupons, message = self.processor.scan_emails()
            self.queue.put(("scan_complete", coupons, message))
        except Exception as e:
            logger.error(f"Error en escaneo: {traceback.format_exc()}")
            self.queue.put(("error", str(e)))
    
    def check_queue(self):
        try:
            while True:
                msg_type, *args = self.queue.get_nowait()
                
                if msg_type == "scan_complete":
                    coupons, message = args
                    self.scanning = False
                    self.progress_bar.stop()
                    self.progress_frame.pack_forget()
                    
                    self.load_notifications()
                    
                    if coupons:
                        self.notifier.notify_new_coupons(len(coupons), coupons)
                        messagebox.showinfo("Escaneo Completado", message)
                    else:
                        messagebox.showinfo("Escaneo Completado", "No se encontraron cupones nuevos")
                
                elif msg_type == "error":
                    error_msg = args[0]
                    self.scanning = False
                    self.progress_bar.stop()
                    self.progress_frame.pack_forget()
                    
                    messagebox.showerror("Error", f"Error en escaneo: {error_msg}")
                    
        except queue.Empty:
            pass
        
        self.root.after(100, self.check_queue)
    
    def copy_selection(self):
        selection = self.tree.selection()
        if not selection:
            return
        
        texts = []
        for item in selection:
            values = self.tree.item(item, "values")
            if values:
                texts.append(values[1])  # Código del cupón
        
        if texts:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(texts))
            messagebox.showinfo("Copiado", f"{len(texts)} cupones copiados")
    
    def show_details(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        
        item = self.tree.item(selection[0])
        values = item['values']
        
        details = f"""
        🎫 Código: {values[1]}
        🏪 Tienda: {values[2]}
        💰 Descuento: {values[3] if values[3] else "No especificado"}
        🔗 URL: {values[4] if values[4] else "No disponible"}
        📊 Confianza: {values[5]}
        📅 Fecha: {values[6]}
        """
        
        messagebox.showinfo("Detalles del Cupón", details)
    
    def clear_all(self):
        if messagebox.askyesno("Confirmar", "¿Eliminar todos los cupones?"):
            cursor = self.db.conn.cursor()
            cursor.execute("DELETE FROM notifications")
            self.db.conn.commit()
            self.load_notifications()
            messagebox.showinfo("Éxito", "Todos los cupones han sido eliminados")
    
    def open_config(self):
        config_window = tk.Toplevel(self.root)
        config_window.title("Configuración")
        config_window.geometry("500x400")
        
        # Pestañas simples
        notebook = ttk.Notebook(config_window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Pestaña General
        general_frame = ttk.Frame(notebook)
        notebook.add(general_frame, text="General")
        
        config = self.db.get_config()
        
        tk.Label(general_frame, text="Intervalo (minutos):").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        intervalo_var = tk.StringVar(value=str(config[1] if config else 30))
        tk.Entry(general_frame, textvariable=intervalo_var, width=10).grid(row=0, column=1, padx=10, pady=10)
        
        # Pestaña Gmail
        gmail_frame = ttk.Frame(notebook)
        notebook.add(gmail_frame, text="Gmail")
        
        tk.Label(gmail_frame, text="Configura Gmail API:", font=("Arial", 11, "bold")).pack(pady=10)
        
        def setup_gmail():
            try:
                tokens = GmailAuthenticator.obtener_tokens_interactivo()
                if tokens:
                    self.db.update_config(
                        client_id=tokens['client_id'],
                        client_secret=tokens['client_secret'],
                        refresh_token=tokens['refresh_token']
                    )
                    messagebox.showinfo("Éxito", "Configuración de Gmail guardada")
                    config_window.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo configurar Gmail: {e}")
        
        tk.Button(gmail_frame, text="Configurar Gmail API", command=setup_gmail).pack(pady=20)
        
        # Botones
        btn_frame = tk.Frame(config_window)
        btn_frame.pack(fill="x", padx=20, pady=10)
        
        def save_config():
            try:
                intervalo = int(intervalo_var.get())
                self.db.update_config(intervalo_minutos=intervalo)
                messagebox.showinfo("Éxito", "Configuración guardada")
                config_window.destroy()
            except ValueError:
                messagebox.showerror("Error", "Intervalo debe ser un número")
        
        tk.Button(btn_frame, text="Guardar", command=save_config).pack(side="right", padx=5)
        tk.Button(btn_frame, text="Cancelar", command=config_window.destroy).pack(side="right", padx=5)
    
    def check_configuration(self):
        config = self.db.get_config()
        if config and not all([config[2], config[3], config[4]]):
            if messagebox.askyesno("Configuración", "Falta configuración de Gmail. ¿Configurar ahora?"):
                self.open_config()
    
    def on_closing(self):
        self.db.close()
        self.root.destroy()

# ==============================================
# EJECUCIÓN PRINCIPAL
# ==============================================
if __name__ == "__main__":
    try:
        # Verificar dependencias esenciales
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            print("Instala las dependencias de Gmail:")
            print("pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")
            print("pip install pillow pytesseract opencv-python numpy")
            exit(1)
        
        root = tk.Tk()
        app = CouponNotifierApp(root)
        
        root.protocol("WM_DELETE_WINDOW", app.on_closing)
        root.mainloop()
        
    except Exception as e:
        logger.critical(f"Error fatal: {traceback.format_exc()}")
        messagebox.showerror("Error", f"Error crítico: {str(e)}")
        raise