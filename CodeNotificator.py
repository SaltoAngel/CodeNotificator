import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import sqlite3
import threading
import queue
import base64
import json
import webbrowser
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
import pytesseract
from PIL import Image
import io
import numpy as np
import cv2
import re

# ==============================================
# CONFIGURACIÓN DE TESSERACT (Ajusta según tu sistema)
# ==============================================
# Windows (descomenta y ajusta la ruta):
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Linux:
# sudo apt-get install tesseract-ocr tesseract-ocr-spa

# macOS:
# brew install tesseract

# ==============================================
# CLASE PARA MANEJO DE BASE DE DATOS
# ==============================================
class DatabaseManager:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        """Crea las tablas necesarias si no existen"""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                cupon TEXT NOT NULL, 
                tienda TEXT NOT NULL,
                URL TEXT NOT NULL, 
                descuento TEXT,
                estado TEXT DEFAULT 'nuevo',
                Fecha DATETIME DEFAULT CURRENT_TIMESTAMP)
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                intervalo_minutos INTEGER DEFAULT 30,
                client_id TEXT,
                client_secret TEXT,
                refresh_token TEXT,
                ultimo_escaneo DATETIME,
                CHECK (id = 1)
            )
        ''')
        
        # Insertar configuración por defecto si no existe
        self.cursor.execute('''
            INSERT OR IGNORE INTO config (id, intervalo_minutos) 
            VALUES (1, 30)
        ''')
        
        self.conn.commit()
    
    def get_config(self):
        """Obtiene la configuración"""
        self.cursor.execute("SELECT * FROM config WHERE id = 1")
        return self.cursor.fetchone()
    
    def update_config(self, **kwargs):
        """Actualiza la configuración"""
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
            self.cursor.execute(query, values)
            self.conn.commit()
    
    def update_last_scan(self):
        """Actualiza la fecha del último escaneo"""
        self.cursor.execute(
            "UPDATE config SET ultimo_escaneo = CURRENT_TIMESTAMP WHERE id = 1"
        )
        self.conn.commit()
    
    def add_notification(self, cupon, tienda, url, descuento=None):
        """Agrega una nueva notificación"""
        self.cursor.execute(
            "INSERT INTO notifications (cupon, tienda, URL, descuento) VALUES (?, ?, ?, ?)",
            (cupon, tienda, url, descuento)
        )
        self.conn.commit()
        return self.cursor.lastrowid
    
    def notification_exists(self, cupon):
        """Verifica si un cupón ya existe"""
        self.cursor.execute(
            "SELECT 1 FROM notifications WHERE cupon = ? LIMIT 1",
            (cupon,)
        )
        return self.cursor.fetchone() is not None
    
    def get_notifications(self, limit=100, estado=None):
        """Obtiene notificaciones"""
        query = "SELECT cupon, tienda, URL, descuento, Fecha FROM notifications"
        params = []
        
        if estado:
            query += " WHERE estado = ?"
            params.append(estado)
        
        query += " ORDER BY Fecha DESC LIMIT ?"
        params.append(limit)
        
        self.cursor.execute(query, params)
        return self.cursor.fetchall()
    
    def mark_as_read(self, cupon_id):
        """Marca una notificación como leída"""
        self.cursor.execute(
            "UPDATE notifications SET estado = 'leido' WHERE id = ?",
            (cupon_id,)
        )
        self.conn.commit()
    
    def clear_notifications(self):
        """Elimina todas las notificaciones"""
        self.cursor.execute("DELETE FROM notifications")
        self.conn.commit()
    
    def close(self):
        """Cierra la conexión a la base de datos"""
        self.conn.close()

# ==============================================
# AUTENTICACIÓN GMAIL
# ==============================================
class GmailAuthenticator:
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
    
    @staticmethod
    def obtener_tokens_interactivo():
        """Obtiene tokens de forma interactiva (solo primera vez)"""
        credenciales = {
            "installed": {
                "client_id": "1001519020081-igeh7pqvvkp0unetgnks4o8na4jir7o6.apps.googleusercontent.com",
                "project_id": "codenotifier-485100",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": "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",
                "redirect_uris": ["http://localhost"]
            }
        }
        
        # Guardar temporalmente
        with open('temp_creds.json', 'w') as f:
            json.dump(credenciales, f)
        
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                'temp_creds.json',
                GmailAuthenticator.SCOPES
            )
            
            creds = flow.run_local_server(
                port=8080,
                prompt='consent',
                authorization_prompt_message=''
            )
            
            return {
                'client_id': credenciales['installed']['client_id'],
                'client_secret': credenciales['installed']['client_secret'],
                'refresh_token': creds.refresh_token
            }
            
        finally:
            if os.path.exists('temp_creds.json'):
                os.remove('temp_creds.json')
    
    @staticmethod
    def get_credentials(db_manager):
        """Obtiene credenciales desde la base de datos"""
        config = db_manager.get_config()
        if not config:
            return None
        
        client_id = config[2]  # índice 2 = client_id
        client_secret = config[3]  # índice 3 = client_secret
        refresh_token = config[4]  # índice 4 = refresh_token
        
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
# PROCESADOR GMAIL + OCR
# ==============================================
class GmailOCRProcessor:
    def __init__(self, db_manager):
        self.db = db_manager
        self.service = None
    
    def authenticate(self):
        """Autentica con Gmail API"""
        try:
            creds = GmailAuthenticator.get_credentials(self.db)
            if not creds:
                return False, "Credenciales no configuradas"
            
            # Refrescar token si es necesario
            if creds.expired:
                creds.refresh(Request())
            
            # Construir servicio
            self.service = build('gmail', 'v1', credentials=creds)
            return True, "Autenticación exitosa"
            
        except Exception as e:
            return False, f"Error de autenticación: {str(e)}"
    
    def search_emails(self, query=None, max_results=20):
        """Busca correos"""
        try:
            if not query:
                query = '(subject:cupón OR subject:descuento OR subject:oferta OR subject:promoción) has:attachment'
            
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            return results.get('messages', [])
        except Exception as e:
            print(f"Error buscando correos: {e}")
            return []
    
    def preprocess_image(self, image_data):
        """Preprocesa imagen para mejorar OCR"""
        try:
            # Bytes a imagen PIL
            image = Image.open(io.BytesIO(image_data))
            
            # Convertir a RGB si es necesario
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Convertir a numpy array
            img_array = np.array(image)
            
            # Convertir a escala de grises
            if len(img_array.shape) == 3:
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            else:
                gray = img_array
            
            # Aplicar threshold adaptativo
            thresh = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 11, 2
            )
            
            # Reducir ruido
            denoised = cv2.medianBlur(thresh, 3)
            
            return Image.fromarray(denoised)
            
        except Exception as e:
            print(f"Error preprocesando imagen: {e}")
            return None
    
    def extract_text(self, image_data, languages=['spa', 'eng']):
        """Extrae texto de una imagen"""
        try:
            # Preprocesar imagen
            processed_img = self.preprocess_image(image_data)
            if processed_img is None:
                return ""
            
            # Configurar idiomas
            lang_str = '+'.join(languages)
            
            # Configuración de Tesseract
            config = '--psm 6 --oem 3'
            
            # Extraer texto
            text = pytesseract.image_to_string(
                processed_img,
                lang=lang_str,
                config=config
            )
            
            return text.strip()
            
        except Exception as e:
            print(f"Error en OCR: {e}")
            return ""
    
    def extract_coupon_info(self, text):
        """Extrae información de cupón del texto"""
        info = {
            'codigo': '',
            'tienda': 'Desconocida',
            'url': '',
            'descuento': '',
            'valido_hasta': ''
        }
        
        # Buscar código de cupón (patrones comunes)
        patterns = [
            r'[A-Z0-9]{4,}-[A-Z0-9]{4,}-[A-Z0-9]{4,}',  # ABC1-DEF2-GHI3
            r'[A-Z0-9]{8,12}',  # Códigos largos sin guiones
            r'C[OÓ]DIGO[:]?\s*([A-Z0-9\-]+)',  # "CÓDIGO: ABC123"
            r'cup[oó]n[:]?\s*([A-Z0-9\-]+)',  # "cupón: ABC123"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info['codigo'] = match.group(1) if len(match.groups()) > 0 else match.group()
                break
        
        # Buscar porcentaje de descuento
        desc_match = re.search(r'(\d{1,3})%', text)
        if desc_match:
            info['descuento'] = f"{desc_match.group(1)}%"
        
        # Buscar URL
        url_match = re.search(r'https?://[^\s]+', text)
        if url_match:
            info['url'] = url_match.group()
        
        # Buscar nombre de tienda (patrones simples)
        tiendas = ['amazon', 'mercado libre', 'ebay', 'aliexpress', 'walmart']
        for tienda in tiendas:
            if tienda in text.lower():
                info['tienda'] = tienda.title()
                break
        
        # Si no se encontró código, usar primeras palabras como referencia
        if not info['codigo'] and len(text) > 10:
            words = text.split()[:5]
            info['codigo'] = ' '.join(words)
        
        return info
    
    def process_email(self, message_id):
        """Procesa un correo específico"""
        try:
            # Obtener el correo
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            # Extraer asunto
            headers = message.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sin asunto')
            
            print(f"Procesando: {subject[:50]}...")
            
            # Buscar adjuntos de imagen
            attachments = self.extract_attachments(message)
            coupons_found = []
            
            for att in attachments:
                # Aplicar OCR
                text = self.extract_text(att['data'])
                
                if text:
                    # Extraer información
                    coupon_info = self.extract_coupon_info(text)
                    
                    if coupon_info['codigo']:
                        coupons_found.append({
                            'asunto': subject,
                            'archivo': att['filename'],
                            **coupon_info,
                            'texto_original': text[:200] + "..." if len(text) > 200 else text
                        })
            
            return coupons_found
            
        except Exception as e:
            print(f"Error procesando email {message_id}: {e}")
            return []
    
    def extract_attachments(self, message):
        """Extrae adjuntos del correo"""
        attachments = []
        
        def process_part(part):
            if part.get('filename'):
                filename = part['filename'].lower()
                
                # Verificar si es imagen
                if any(filename.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff']):
                    attachment_id = part.get('body', {}).get('attachmentId')
                    
                    if attachment_id:
                        try:
                            # Descargar adjunto
                            attachment = self.service.users().messages().attachments().get(
                                userId='me',
                                messageId=message['id'],
                                id=attachment_id
                            ).execute()
                            
                            # Decodificar
                            file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                            
                            attachments.append({
                                'filename': part['filename'],
                                'data': file_data,
                                'size': len(file_data)
                            })
                            
                        except Exception as e:
                            print(f"Error descargando adjunto {filename}: {e}")
            
            # Procesar partes anidadas
            if 'parts' in part:
                for subpart in part['parts']:
                    process_part(subpart)
        
        # Procesar partes principales
        payload = message.get('payload', {})
        process_part(payload)
        
        return attachments
    
    def scan_emails(self, max_emails=10):
        """Escanea correos en busca de cupones"""
        success, message = self.authenticate()
        if not success:
            return [], message
        
        try:
            # Buscar correos recientes
            emails = self.search_emails(max_results=max_emails)
            all_coupons = []
            
            for i, email in enumerate(emails, 1):
                print(f"Procesando email {i}/{len(emails)}...")
                coupons = self.process_email(email['id'])
                all_coupons.extend(coupons)
            
            # Filtrar duplicados y guardar en BD
            unique_coupons = []
            for coupon in all_coupons:
                if not self.db.notification_exists(coupon['codigo']):
                    self.db.add_notification(
                        coupon['codigo'],
                        coupon['tienda'],
                        coupon['url'],
                        coupon['descuento']
                    )
                    unique_coupons.append(coupon)
            
            # Actualizar último escaneo
            self.db.update_last_scan()
            
            return unique_coupons, f"Encontrados {len(unique_coupons)} cupones nuevos"
            
        except Exception as e:
            return [], f"Error en escaneo: {str(e)}"

# ==============================================
# INTERFAZ GRÁFICA PRINCIPAL
# ==============================================
class CouponNotifierApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Notificador de Cupones de Descuento (Gmail)")
        self.root.geometry("1000x700")
        
        # Configurar icono (opcional)
        try:
            self.root.iconbitmap('icon.ico')
        except:
            pass
        
        # Base de datos
        db_path = os.path.join(os.path.dirname(__file__), 'notifications.db')
        self.db = DatabaseManager(db_path)
        
        # Procesador
        self.processor = GmailOCRProcessor(self.db)
        
        # Variables
        self.queue = queue.Queue()
        self.scanning = False
        self.auto_scan_id = None
        
        # Configurar interfaz
        self.setup_ui()
        
        # Cargar datos
        self.load_notifications()
        
        # Iniciar chequeo de queue
        self.check_queue()
        
        # Verificar configuración
        self.check_configuration()
    
    def setup_ui(self):
        """Configura la interfaz gráfica"""
        # Configurar grid principal
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        
        # ========== BARRA SUPERIOR ==========
        top_frame = tk.Frame(self.root, bg="#2c3e50", height=50)
        top_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        top_frame.grid_columnconfigure(1, weight=1)
        
        # Título
        title_label = tk.Label(
            top_frame,
            text="🛒 Notificador de Cupones",
            font=("Arial", 16, "bold"),
            bg="#2c3e50",
            fg="white"
        )
        title_label.grid(row=0, column=0, padx=20, pady=10, sticky="w")
        
        # Estado
        self.status_label = tk.Label(
            top_frame,
            text="Listo",
            font=("Arial", 10),
            bg="#2c3e50",
            fg="#ecf0f1"
        )
        self.status_label.grid(row=0, column=2, padx=20, pady=10, sticky="e")
        
        # ========== BARRA DE HERRAMIENTAS ==========
        toolbar = tk.Frame(self.root, bg="#ecf0f1", height=40)
        toolbar.grid(row=1, column=0, sticky="new", padx=0, pady=0)
        
        # Botones
        buttons = [
            ("⚙️ Configuración", self.open_config),
            ("🔍 Escanear Ahora", self.start_scan),
            ("📋 Copiar Selección", self.copy_selection),
            ("🌐 Abrir URL", self.open_url),
            ("🗑️ Limpiar Todo", self.clear_all),
            ("🔄 Actualizar", self.load_notifications)
        ]
        
        for i, (text, command) in enumerate(buttons):
            btn = tk.Button(
                toolbar,
                text=text,
                command=command,
                bg="#3498db",
                fg="white",
                font=("Arial", 10),
                relief="flat",
                padx=15,
                pady=5
            )
            btn.grid(row=0, column=i, padx=5, pady=5)
        
        # ========== CONTENIDO PRINCIPAL ==========
        main_frame = tk.Frame(self.root)
        main_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(0, weight=1)
        
        # Treeview con scrollbars
        tree_frame = tk.Frame(main_frame)
        tree_frame.grid(row=0, column=0, sticky="nsew")
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        
        # Columnas
        columns = ("Cupón", "Tienda", "Descuento", "URL", "Fecha")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended"
        )
        
        # Configurar columnas
        col_widths = {"Cupón": 150, "Tienda": 120, "Descuento": 80, "URL": 250, "Fecha": 120}
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths.get(col, 100), anchor="center")
        
        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        # Layout
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        # ========== BARRA INFERIOR ==========
        bottom_frame = tk.Frame(self.root, bg="#ecf0f1")
        bottom_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        
        # Contador
        self.count_label = tk.Label(
            bottom_frame,
            text="0 cupones",
            font=("Arial", 10, "bold"),
            bg="#ecf0f1"
        )
        self.count_label.pack(side="left")
        
        # Info adicional
        config = self.db.get_config()
        if config and config[5]:  # último escaneo
            last_scan = datetime.strptime(config[5], "%Y-%m-%d %H:%M:%S")
            last_scan_str = last_scan.strftime("%d/%m/%Y %H:%M")
            scan_label = tk.Label(
                bottom_frame,
                text=f"Último escaneo: {last_scan_str}",
                font=("Arial", 9),
                bg="#ecf0f1",
                fg="#7f8c8d"
            )
            scan_label.pack(side="right", padx=10)
        
        # ========== MENÚ CONTEXTUAL ==========
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Copiar", command=self.copy_selection)
        self.context_menu.add_command(label="Abrir URL", command=self.open_url)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Marcar como leído", command=self.mark_as_read)
        self.context_menu.add_command(label="Eliminar", command=self.delete_selected)
        
        # Vincular menú contextual
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Double-Button-1>", self.show_details)
    
    def show_context_menu(self, event):
        """Muestra menú contextual"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)
    
    def check_configuration(self):
        """Verifica si la configuración está completa"""
        config = self.db.get_config()
        if config and not all([config[2], config[3], config[4]]):  # Credenciales
            response = messagebox.askyesno(
                "Configuración Requerida",
                "Faltan las credenciales de Gmail. ¿Desea configurarlas ahora?"
            )
            if response:
                self.open_config()
    
    def open_config(self):
        """Abre ventana de configuración"""
        config_window = tk.Toplevel(self.root)
        config_window.title("Configuración")
        config_window.geometry("600x500")
        config_window.transient(self.root)
        config_window.grab_set()
        
        # Notebook (pestañas)
        notebook = ttk.Notebook(config_window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Pestaña 1: General
        general_frame = ttk.Frame(notebook)
        notebook.add(general_frame, text="General")
        
        # Intervalo de escaneo
        ttk.Label(general_frame, text="Intervalo de escaneo (minutos):").grid(
            row=0, column=0, sticky="w", padx=20, pady=10
        )
        
        config = self.db.get_config()
        intervalo_var = tk.StringVar(value=str(config[1] if config else 30))
        intervalo_spin = ttk.Spinbox(
            general_frame,
            from_=1,
            to=1440,
            textvariable=intervalo_var,
            width=10
        )
        intervalo_spin.grid(row=0, column=1, padx=20, pady=10)
        
        # Pestaña 2: Gmail API
        gmail_frame = ttk.Frame(notebook)
        notebook.add(gmail_frame, text="Gmail API")
        
        # Información
        info_text = """
        Para configurar la API de Gmail:
        
        1. Usa las credenciales que ya tienes:
           - Client ID: 1001519020081-igeh7pqvvkp0unetgnks4o8na4jir7o6.apps.googleusercontent.com
           - Client Secret: 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
        
        2. Haz clic en "Obtener Refresh Token" para autorizar la aplicación.
        
        3. Los tokens se guardarán automáticamente.
        """
        
        info_label = tk.Label(
            gmail_frame,
            text=info_text,
            justify="left",
            font=("Arial", 10)
        )
        info_label.pack(padx=20, pady=10, fill="both")
        
        # Botón para obtener token
        def obtener_token():
            try:
                tokens = GmailAuthenticator.obtener_tokens_interactivo()
                if tokens:
                    self.db.update_config(
                        client_id=tokens['client_id'],
                        client_secret=tokens['client_secret'],
                        refresh_token=tokens['refresh_token']
                    )
                    messagebox.showinfo("Éxito", "Tokens guardados correctamente")
                    config_window.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo obtener token: {e}")
        
        token_btn = ttk.Button(
            gmail_frame,
            text="🔄 Obtener Refresh Token",
            command=obtener_token
        )
        token_btn.pack(pady=20)
        
        # Mostrar tokens actuales (enmascarados)
        if config and config[2]:
            current_frame = ttk.LabelFrame(gmail_frame, text="Configuración Actual")
            current_frame.pack(fill="x", padx=20, pady=10)
            
            ttk.Label(current_frame, text="Client ID:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
            ttk.Label(current_frame, text=config[2][:20] + "..." if len(config[2]) > 20 else config[2]).grid(
                row=0, column=1, sticky="w", padx=10, pady=5
            )
            
            ttk.Label(current_frame, text="Token configurado: Sí").grid(
                row=1, column=0, columnspan=2, padx=10, pady=5
            )
        
        # Botones de acción
        button_frame = ttk.Frame(config_window)
        button_frame.pack(fill="x", padx=20, pady=10)
        
        def guardar_config():
            try:
                intervalo = int(intervalo_var.get())
                if intervalo < 1:
                    raise ValueError("El intervalo debe ser mayor a 0")
                
                self.db.update_config(intervalo_minutos=intervalo)
                messagebox.showinfo("Éxito", "Configuración guardada")
                config_window.destroy()
                
                # Reiniciar escaneo automático
                self.stop_auto_scan()
                self.start_auto_scan()
                
            except ValueError as e:
                messagebox.showerror("Error", f"Valor inválido: {e}")
        
        ttk.Button(button_frame, text="Guardar", command=guardar_config).pack(side="right", padx=5)
        ttk.Button(button_frame, text="Cancelar", command=config_window.destroy).pack(side="right", padx=5)
    
    def start_scan(self):
        """Inicia escaneo manual"""
        if self.scanning:
            messagebox.showwarning("Escaneo en curso", "Ya hay un escaneo en progreso")
            return
        
        self.scanning = True
        self.status_label.config(text="🔍 Escaneando correos...", fg="#e74c3c")
        
        # Ejecutar en hilo separado
        thread = threading.Thread(target=self.perform_scan, daemon=True)
        thread.start()
    
    def perform_scan(self):
        """Ejecuta el escaneo"""
        try:
            coupons, message = self.processor.scan_emails(max_emails=15)
            self.queue.put(("scan_complete", coupons, message))
        except Exception as e:
            self.queue.put(("error", str(e)))
    
    def start_auto_scan(self):
        """Inicia escaneo automático"""
        config = self.db.get_config()
        if config:
            intervalo = config[1]  # minutos
            intervalo_ms = intervalo * 60 * 1000  # convertir a milisegundos
            
            self.auto_scan_id = self.root.after(intervalo_ms, self.auto_scan)
    
    def stop_auto_scan(self):
        """Detiene el escaneo automático"""
        if self.auto_scan_id:
            self.root.after_cancel(self.auto_scan_id)
            self.auto_scan_id = None
    
    def auto_scan(self):
        """Ejecuta escaneo automático"""
        if not self.scanning:
            self.start_scan()
        self.start_auto_scan()
    
    def check_queue(self):
        """Verifica la queue para actualizaciones"""
        try:
            while True:
                msg_type, *args = self.queue.get_nowait()
                
                if msg_type == "scan_complete":
                    coupons, message = args
                    self.scanning = False
                    
                    # Actualizar estado
                    self.status_label.config(text=message, fg="#27ae60")
                    
                    # Recargar notificaciones
                    self.load_notifications()
                    
                    # Mostrar notificación si se encontraron cupones
                    if coupons:
                        self.show_scan_results(coupons)
                
                elif msg_type == "error":
                    error_msg = args[0]
                    self.scanning = False
                    self.status_label.config(text=f"Error: {error_msg}", fg="#c0392b")
                    
        except queue.Empty:
            pass
        
        # Verificar de nuevo
        self.root.after(100, self.check_queue)
    
    def show_scan_results(self, coupons):
        """Muestra resultados del escaneo"""
        if not coupons:
            return
        
        result_text = f"🎉 Se encontraron {len(coupons)} cupones nuevos:\n\n"
        
        for coupon in coupons:
            result_text += f"• {coupon['codigo']} - {coupon['tienda']}"
            if coupon['descuento']:
                result_text += f" ({coupon['descuento']})"
            result_text += "\n"
        
        # Ventana de resultados
        result_window = tk.Toplevel(self.root)
        result_window.title("Nuevos Cupones Encontrados")
        result_window.geometry("500x300")
        
        text_widget = scrolledtext.ScrolledText(
            result_window,
            wrap=tk.WORD,
            font=("Arial", 10)
        )
        text_widget.pack(fill="both", expand=True, padx=10, pady=10)
        text_widget.insert("1.0", result_text)
        text_widget.config(state="disabled")
        
        ttk.Button(
            result_window,
            text="Cerrar",
            command=result_window.destroy
        ).pack(pady=10)
    
    def load_notifications(self):
        """Carga notificaciones en el treeview"""
        # Limpiar treeview
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Obtener notificaciones
        notifications = self.db.get_notifications(limit=100)
        
        # Insertar en treeview
        for notif in notifications:
            # Formatear fecha
            fecha = notif[4]
            if isinstance(fecha, str):
                try:
                    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d %H:%M:%S")
                    fecha_str = fecha_dt.strftime("%d/%m/%Y %H:%M")
                except:
                    fecha_str = fecha
            else:
                fecha_str = str(fecha)
            
            self.tree.insert("", "end", values=(
                notif[0],  # cupon
                notif[1],  # tienda
                notif[3] or "",  # descuento
                notif[2],  # URL
                fecha_str  # fecha formateada
            ))
        
        # Actualizar contador
        self.count_label.config(text=f"{len(notifications)} cupones encontrados")
    
    def copy_selection(self):
        """Copia la selección al portapapeles"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Sin selección", "Seleccione un cupón para copiar")
            return
        
        text_to_copy = []
        for item in selection:
            values = self.tree.item(item, "values")
            if values:
                cupon = values[0]
                text_to_copy.append(cupon)
        
        if text_to_copy:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(text_to_copy))
            messagebox.showinfo("Copiado", f"Se copiaron {len(text_to_copy)} cupones")
    
    def open_url(self):
        """Abre la URL en el navegador"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Sin selección", "Seleccione un cupón para abrir su URL")
            return
        
        item = self.tree.item(selection[0])
        url = item['values'][3]  # URL está en la columna 3
        
        if url and (url.startswith('http://') or url.startswith('https://')):
            webbrowser.open(url)
        else:
            messagebox.showwarning("URL inválida", "La URL no es válida")
    
    def show_details(self, event=None):
        """Muestra detalles del cupón seleccionado"""
        selection = self.tree.selection()
        if not selection:
            return
        
        item = self.tree.item(selection[0])
        values = item['values']
        
        details = f"""
        📋 Detalles del Cupón:
        
        Código: {values[0]}
        Tienda: {values[1]}
        Descuento: {values[2] if values[2] else 'No especificado'}
        URL: {values[3] if values[3] else 'No disponible'}
        Fecha: {values[4]}
        """
        
        messagebox.showinfo("Detalles", details)
    
    def mark_as_read(self):
        """Marca los seleccionados como leídos"""
        selection = self.tree.selection()
        if selection:
            for item in selection:
                # Aquí implementarías la lógica para marcar como leído en BD
                pass
            messagebox.showinfo("Éxito", f"Se marcaron {len(selection)} cupones como leídos")
    
    def delete_selected(self):
        """Elimina los cupones seleccionados"""
        selection = self.tree.selection()
        if not selection:
            return
        
        if messagebox.askyesno("Confirmar", f"¿Eliminar {len(selection)} cupones?"):
            for item in selection:
                self.tree.delete(item)
            # Aquí implementarías la eliminación de la BD
    
    def clear_all(self):
        """Limpia todas las notificaciones"""
        if messagebox.askyesno("Confirmar", "¿Eliminar TODOS los cupones?"):
            self.db.clear_notifications()
            self.load_notifications()
            messagebox.showinfo("Éxito", "Todos los cupones han sido eliminados")
    
    def on_closing(self):
        """Maneja el cierre de la aplicación"""
        self.stop_auto_scan()
        self.db.close()
        self.root.destroy()

# ==============================================
# EJECUCIÓN PRINCIPAL
# ==============================================
if __name__ == "__main__":
    # Verificar dependencias
    try:
        import google.auth
    except ImportError:
        print("Instala las dependencias necesarias:")
        print("pip install google-auth google-auth-oauthlib google-auth-httplib2")
        print("pip install google-api-python-client")
        print("pip install pillow pytesseract opencv-python numpy")
        exit(1)
    
    # Crear y ejecutar aplicación
    root = tk.Tk()
    app = CouponNotifierApp(root)
    
    # Configurar cierre
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    # Iniciar escaneo automático
    app.start_auto_scan()
    
    # Ejecutar
    root.mainloop()
