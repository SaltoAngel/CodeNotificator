import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import os
import sqlite3
import threading
import queue
import base64
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import pytesseract
from PIL import Image
import io
import numpy as np
import cv2
import re

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
                Fecha DATETIME DEFAULT CURRENT_TIMESTAMP)
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                TmpTime INTEGER DEFAULT 30,
                GmailKey TEXT,
                ClientId TEXT,
                ClientSecret TEXT,
                RefreshToken TEXT,
                CHECK (id = 1)
            )
        ''')
        
        # Insertar configuración por defecto si no existe
        self.cursor.execute('''
            INSERT OR IGNORE INTO config (id, TmpTime) 
            VALUES (1, 30)
        ''')
        
        self.conn.commit()
    
    def get_config(self):
        """Obtiene la configuración"""
        self.cursor.execute("SELECT * FROM config WHERE id = 1")
        return self.cursor.fetchone()
    
    def update_config(self, tmp_time=None, gmail_key=None, client_id=None, client_secret=None, refresh_token=None):
        """Actualiza la configuración"""
        update_fields = []
        params = []
        
        if tmp_time is not None:
            update_fields.append("TmpTime = ?")
            params.append(tmp_time)
        if gmail_key is not None:
            update_fields.append("GmailKey = ?")
            params.append(gmail_key)
        if client_id is not None:
            update_fields.append("ClientId = ?")
            params.append(client_id)
        if client_secret is not None:
            update_fields.append("ClientSecret = ?")
            params.append(client_secret)
        if refresh_token is not None:
            update_fields.append("RefreshToken = ?")
            params.append(refresh_token)
        
        if update_fields:
            query = f"UPDATE config SET {', '.join(update_fields)} WHERE id = 1"
            self.cursor.execute(query, params)
            self.conn.commit()
    
    def add_notification(self, cupon, tienda, url):
        """Agrega una nueva notificación"""
        self.cursor.execute(
            "INSERT INTO notifications (cupon, tienda, URL) VALUES (?, ?, ?)",
            (cupon, tienda, url)
        )
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_notifications(self, limit=100):
        """Obtiene notificaciones"""
        self.cursor.execute(
            "SELECT cupon, tienda, URL, Fecha FROM notifications ORDER BY Fecha DESC LIMIT ?",
            (limit,)
        )
        return self.cursor.fetchall()
    
    def clear_notifications(self):
        """Elimina todas las notificaciones"""
        self.cursor.execute("DELETE FROM notifications")
        self.conn.commit()
    
    def close(self):
        """Cierra la conexión a la base de datos"""
        self.conn.close()

# ==============================================
# CLASE PARA PROCESAMIENTO DE GMAIL Y OCR
# ==============================================
class GmailOCRProcessor:
    def __init__(self, db_manager):
        self.db = db_manager
        self.service = None
        self.creds = None
    
    def authenticate(self):
        """Autentica con Gmail API"""
        try:
            config = self.db.get_config()
            if not config:
                return False, "No hay configuración disponible"
            
            # Índices de la configuración
            CLIENT_ID = config[3] if len(config) > 3 else None
            CLIENT_SECRET = config[4] if len(config) > 4 else None
            REFRESH_TOKEN = config[5] if len(config) > 5 else None
            
            if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
                return False, "Faltan credenciales de Gmail. Configúralas primero."
            
            # Crear credenciales
            self.creds = Credentials(
                token=None,
                refresh_token=REFRESH_TOKEN,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                scopes=['https://www.googleapis.com/auth/gmail.readonly']
            )
            
            # Refrescar token si es necesario
            if self.creds.expired:
                self.creds.refresh(Request())
            
            # Construir servicio
            self.service = build('gmail', 'v1', credentials=self.creds)
            return True, "Autenticación exitosa"
            
        except Exception as e:
            return False, f"Error de autenticación: {str(e)}"
    
    def search_coupon_emails(self, max_results=20):
        """Busca correos que puedan contener cupones"""
        try:
            # Palabras clave para buscar cupones
            keywords = ['cupón', 'descuento', 'oferta', 'promoción', 'coupon', 'discount', 'sale']
            query = ' OR '.join([f'subject:{kw}' for kw in keywords])
            query += ' has:attachment'
            
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            return results.get('messages', [])
        except Exception as e:
            print(f"Error buscando correos: {e}")
            return []
    
    def extract_text_from_image(self, image_data):
        """Extrae texto de una imagen usando OCR"""
        try:
            # Convertir bytes a imagen
            image = Image.open(io.BytesIO(image_data))
            
            # Preprocesar imagen para mejorar OCR
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Convertir a array numpy para OpenCV
            img_array = np.array(image)
            
            # Convertir a escala de grises
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            
            # Aplicar threshold adaptativo
            thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                          cv2.THRESH_BINARY, 11, 2)
            
            # Convertir de vuelta a PIL Image
            processed_img = Image.fromarray(thresh)
            
            # Configurar Tesseract para español
            pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
            
            # Extraer texto
            text = pytesseract.image_to_string(processed_img, lang='spa+eng')
            
            return text.strip()
        except Exception as e:
            return f"Error en OCR: {str(e)}"
    
    def extract_coupon_info(self, text):
        """Extrae información de cupón del texto"""
        # Patrones para buscar cupones
        patterns = {
            'cupon': r'[A-Z0-9]{4,}-[A-Z0-9]{4,}-[A-Z0-9]{4,}|[A-Z0-9]{8,}',
            'descuento': r'(\d{1,3})%|\$(\d+(\.\d{2})?) off|descuento.*?(\d{1,3})%',
            'tienda': r'from\s+(.+?)\s+|at\s+(.+?)\s+|en\s+(.+?)\s+',
            'url': r'https?://[^\s]+|www\.[^\s]+'
        }
        
        info = {
            'cupon': '',
            'tienda': 'Desconocida',
            'url': '',
            'descuento': ''
        }
        
        # Buscar código de cupón
        cupon_match = re.search(patterns['cupon'], text, re.IGNORECASE)
        if cupon_match:
            info['cupon'] = cupon_match.group()
        
        # Buscar descuento
        desc_match = re.search(patterns['descuento'], text, re.IGNORECASE)
        if desc_match:
            info['descuento'] = desc_match.group()
        
        # Buscar URL
        url_match = re.search(patterns['url'], text, re.IGNORECASE)
        if url_match:
            info['url'] = url_match.group()
        
        # Si no se encontró código de cupón, usar parte del texto
        if not info['cupon'] and len(text) > 10:
            info['cupon'] = text[:20] + "..."
        
        return info
    
    def process_email_attachments(self, message_id):
        """Procesa adjuntos de un correo"""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            attachments_info = []
            
            # Obtener partes del mensaje
            payload = message.get('payload', {})
            parts = payload.get('parts', [])
            
            for part in parts:
                if part.get('filename'):
                    filename = part['filename'].lower()
                    
                    # Verificar si es imagen
                    if filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                        attachment_id = part.get('body', {}).get('attachmentId')
                        
                        if attachment_id:
                            # Descargar adjunto
                            attachment = self.service.users().messages().attachments().get(
                                userId='me',
                                messageId=message_id,
                                id=attachment_id
                            ).execute()
                            
                            # Decodificar
                            file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                            
                            # Aplicar OCR
                            text = self.extract_text_from_image(file_data)
                            
                            # Extraer información del cupón
                            coupon_info = self.extract_coupon_info(text)
                            
                            if coupon_info['cupon']:
                                attachments_info.append({
                                    'filename': part['filename'],
                                    'text': text,
                                    'coupon_info': coupon_info
                                })
            
            return attachments_info
        except Exception as e:
            print(f"Error procesando adjuntos: {e}")
            return []
    
    def scan_for_coupons(self, max_emails=10):
        """Escanea correos en busca de cupones"""
        success, message = self.authenticate()
        if not success:
            return [], message
        
        try:
            # Buscar correos con posibles cupones
            emails = self.search_coupon_emails(max_results=max_emails)
            coupons_found = []
            
            for email in emails:
                # Procesar adjuntos del correo
                attachments = self.process_email_attachments(email['id'])
                
                for att in attachments:
                    coupon_info = att['coupon_info']
                    
                    # Guardar en base de datos
                    self.db.add_notification(
                        coupon_info['cupon'],
                        coupon_info['tienda'],
                        coupon_info['url']
                    )
                    
                    coupons_found.append({
                        'cupon': coupon_info['cupon'],
                        'tienda': coupon_info['tienda'],
                        'url': coupon_info['url'],
                        'descuento': coupon_info['descuento'],
                        'texto': att['text'][:100] + "..." if len(att['text']) > 100 else att['text']
                    })
            
            return coupons_found, f"Encontrados {len(coupons_found)} cupones"
            
        except Exception as e:
            return [], f"Error escaneando: {str(e)}"

# ==============================================
# INTERFAZ GRÁFICA
# ==============================================
class CouponNotifierApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Notificador de Cupones de Descuento (Gmail)")
        self.root.geometry("900x600")
        
        # Base de datos
        db_path = os.path.join(os.path.dirname(__file__), 'notifications.db')
        self.db = DatabaseManager(db_path)
        
        # Procesador Gmail
        self.processor = GmailOCRProcessor(self.db)
        
        # Queue para comunicación entre hilos
        self.queue = queue.Queue()
        
        # Variables de estado
        self.scanning = False
        self.auto_scan_id = None
        
        # Configurar interfaz
        self.setup_ui()
        
        # Cargar notificaciones
        self.load_notifications()
        
        # Iniciar chequeo de queue
        self.check_queue()
        
        # Iniciar escaneo automático si está configurado
        self.start_auto_scan()
    
    def setup_ui(self):
        """Configura la interfaz de usuario"""
        # ========== BARRA DE NAVEGACIÓN ==========
        navbar = tk.Frame(self.root, bg="lightgrey", height=40)
        navbar.pack(side="top", fill="x")
        
        # Botón Configuración
        btn_config = tk.Button(navbar, text="Configuración", command=self.open_config_window)
        btn_config.pack(side="left", padx=5, pady=5)
        
        # Botón Escanear Ahora
        btn_scan = tk.Button(navbar, text="Escanear Ahora", command=self.start_manual_scan)
        btn_scan.pack(side="left", padx=5, pady=5)
        
        # Botón Limpiar
        btn_clear = tk.Button(navbar, text="Limpiar Lista", command=self.clear_notifications)
        btn_clear.pack(side="left", padx=5, pady=5)
        
        # Etiqueta de estado
        self.status_label = tk.Label(navbar, text="Listo", bg="lightgrey")
        self.status_label.pack(side="right", padx=10, pady=5)
        
        # ========== CONTENIDO PRINCIPAL ==========
        main_frame = ttk.Frame(self.root, padding=(10, 10, 10, 10))
        main_frame.pack(fill="both", expand=True)
        
        # Treeview para notificaciones
        columns = ("Cupón", "Tienda", "URL", "Fecha")
        self.tree = ttk.Treeview(main_frame, columns=columns, show="headings", height=20)
        
        # Configurar columnas
        column_widths = {"Cupón": 150, "Tienda": 150, "URL": 300, "Fecha": 150}
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=column_widths.get(col, 100))
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        # Layout
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        
        # Configurar expansión
        main_frame.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        
        # ========== BARRA INFERIOR ==========
        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(side="bottom", fill="x", padx=10, pady=5)
        
        # Contador de notificaciones
        self.count_label = tk.Label(bottom_frame, text="0 notificaciones")
        self.count_label.pack(side="left")
        
        # Botón para mostrar detalles
        btn_details = tk.Button(bottom_frame, text="Ver Detalles", command=self.show_details)
        btn_details.pack(side="right", padx=5)
        
        # Botón para abrir URL
        btn_open_url = tk.Button(bottom_frame, text="Abrir URL", command=self.open_url)
        btn_open_url.pack(side="right", padx=5)
    
    def open_config_window(self):
        """Abre ventana de configuración"""
        config_window = tk.Toplevel(self.root)
        config_window.title("Configuración")
        config_window.geometry("500x400")
        
        # Obtener configuración actual
        config = self.db.get_config()
        
        # Frame principal
        main_frame = ttk.Frame(config_window, padding=20)
        main_frame.pack(fill="both", expand=True)
        
        # Título
        ttk.Label(main_frame, text="Configuración del Sistema", font=("Arial", 14, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 20)
        )
        
        # Intervalo de escaneo
        ttk.Label(main_frame, text="Intervalo (minutos):").grid(row=1, column=0, sticky="w", pady=5)
        tmp_time_var = tk.StringVar(value=str(config[1] if config else 30))
        entry_interval = ttk.Entry(main_frame, textvariable=tmp_time_var, width=10)
        entry_interval.grid(row=1, column=1, sticky="w", pady=5)
        
        # Separador
        ttk.Separator(main_frame, orient="horizontal").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=20
        )
        
        # Configuración de Gmail
        ttk.Label(main_frame, text="Configuración de Gmail API", font=("Arial", 11, "bold")).grid(
            row=3, column=0, columnspan=2, pady=(0, 10), sticky="w"
        )
        
        ttk.Label(main_frame, text="Client ID:").grid(row=4, column=0, sticky="w", pady=5)
        client_id_var = tk.StringVar(value=config[3] if config and len(config) > 3 else "")
        entry_client_id = ttk.Entry(main_frame, textvariable=client_id_var, width=40)
        entry_client_id.grid(row=4, column=1, sticky="w", pady=5)
        
        ttk.Label(main_frame, text="Client Secret:").grid(row=5, column=0, sticky="w", pady=5)
        client_secret_var = tk.StringVar(value=config[4] if config and len(config) > 4 else "")
        entry_client_secret = ttk.Entry(main_frame, textvariable=client_secret_var, width=40)
        entry_client_secret.grid(row=5, column=1, sticky="w", pady=5)
        
        ttk.Label(main_frame, text="Refresh Token:").grid(row=6, column=0, sticky="w", pady=5)
        refresh_token_var = tk.StringVar(value=config[5] if config and len(config) > 5 else "")
        entry_refresh_token = ttk.Entry(main_frame, textvariable=refresh_token_var, width=40)
        entry_refresh_token.grid(row=6, column=1, sticky="w", pady=5)
        
        # Botón para obtener token (simplificado)
        def get_token_help():
            messagebox.showinfo(
                "Ayuda para obtener tokens",
                "1. Ve a Google Cloud Console\n"
                "2. Crea un proyecto y habilita Gmail API\n"
                "3. Crea credenciales OAuth 2.0\n"
                "4. Usa el tipo 'Aplicación de escritorio'\n"
                "5. Descarga el JSON y copia los valores"
            )
        
        ttk.Button(main_frame, text="¿Cómo obtener tokens?", command=get_token_help).grid(
            row=7, column=0, columnspan=2, pady=10
        )
        
        # Botones de acción
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=8, column=0, columnspan=2, pady=20)
        
        def save_config():
            try:
                # Validar intervalo
                tmp_time = int(tmp_time_var.get())
                if tmp_time < 1:
                    raise ValueError("El intervalo debe ser al menos 1 minuto")
                
                # Actualizar configuración
                self.db.update_config(
                    tmp_time=tmp_time,
                    client_id=client_id_var.get() or None,
                    client_secret=client_secret_var.get() or None,
                    refresh_token=refresh_token_var.get() or None
                )
                
                messagebox.showinfo("Éxito", "Configuración guardada correctamente")
                config_window.destroy()
                
                # Reiniciar escaneo automático
                self.stop_auto_scan()
                self.start_auto_scan()
                
            except ValueError as e:
                messagebox.showerror("Error", f"Valor inválido: {e}")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar: {e}")
        
        def test_connection():
            success, message = self.processor.authenticate()
            if success:
                messagebox.showinfo("Conexión exitosa", "La conexión con Gmail API es correcta")
            else:
                messagebox.showerror("Error de conexión", message)
        
        ttk.Button(button_frame, text="Probar Conexión", command=test_connection).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Guardar", command=save_config).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Cancelar", command=config_window.destroy).pack(side="left", padx=5)
    
    def load_notifications(self):
        """Carga notificaciones en el treeview"""
        # Limpiar treeview
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Obtener notificaciones
        notifications = self.db.get_notifications()
        
        # Insertar en treeview
        for notif in notifications:
            self.tree.insert("", "end", values=notif)
        
        # Actualizar contador
        self.count_label.config(text=f"{len(notifications)} notificaciones")
    
    def start_manual_scan(self):
        """Inicia un escaneo manual"""
        if self.scanning:
            messagebox.showwarning("Escaneo en curso", "Ya hay un escaneo en progreso")
            return
        
        self.scanning = True
        self.status_label.config(text="Escaneando...", fg="blue")
        
        # Ejecutar en hilo separado
        thread = threading.Thread(target=self.perform_scan, daemon=True)
        thread.start()
    
    def perform_scan(self):
        """Ejecuta el escaneo (en hilo separado)"""
        try:
            coupons, message = self.processor.scan_for_coupons(max_emails=10)
            
            # Enviar resultados a través de la queue
            self.queue.put(("scan_complete", coupons, message))
            
        except Exception as e:
            self.queue.put(("error", f"Error en escaneo: {str(e)}"))
    
    def start_auto_scan(self):
        """Inicia el escaneo automático"""
        config = self.db.get_config()
        if config:
            interval = config[1]  # TmpTime en minutos
            
            # Convertir a milisegundos
            interval_ms = interval * 60 * 1000
            
            # Programar próximo escaneo
            self.auto_scan_id = self.root.after(interval_ms, self.auto_scan)
    
    def stop_auto_scan(self):
        """Detiene el escaneo automático"""
        if self.auto_scan_id:
            self.root.after_cancel(self.auto_scan_id)
            self.auto_scan_id = None
    
    def auto_scan(self):
        """Ejecuta escaneo automático"""
        if not self.scanning:
            self.start_manual_scan()
        
        # Programar próximo escaneo
        self.start_auto_scan()
    
    def check_queue(self):
        """Verifica la queue para actualizaciones desde el hilo"""
        try:
            while True:
                msg_type, *args = self.queue.get_nowait()
                
                if msg_type == "scan_complete":
                    coupons, message = args
                    self.scanning = False
                    
                    # Actualizar interfaz
                    self.status_label.config(text=message, fg="green")
                    self.load_notifications()
                    
                    # Mostrar notificación si se encontraron cupones
                    if coupons:
                        self.show_scan_results(coupons)
                
                elif msg_type == "error":
                    error_msg = args[0]
                    self.scanning = False
                    self.status_label.config(text=error_msg, fg="red")
                    
        except queue.Empty:
            pass
        
        # Verificar de nuevo después de 100ms
        self.root.after(100, self.check_queue)
    
    def show_scan_results(self, coupons):
        """Muestra resultados del escaneo"""
        result_text = f"Se encontraron {len(coupons)} cupones:\n\n"
        
        for i, coupon in enumerate(coupons, 1):
            result_text += f"{i}. {coupon['cupon']} - {coupon['tienda']}\n"
            if coupon['descuento']:
                result_text += f"   Descuento: {coupon['descuento']}\n"
            result_text += f"   URL: {coupon['url'][:50]}...\n\n"
        
        # Mostrar en ventana emergente
        result_window = tk.Toplevel(self.root)
        result_window.title("Resultados del Escaneo")
        result_window.geometry("600x400")
        
        text_widget = scrolledtext.ScrolledText(result_window, wrap=tk.WORD)
        text_widget.pack(fill="both", expand=True, padx=10, pady=10)
        text_widget.insert("1.0", result_text)
        text_widget.config(state="disabled")
        
        ttk.Button(result_window, text="Cerrar", command=result_window.destroy).pack(pady=10)
    
    def show_details(self):
        """Muestra detalles de la notificación seleccionada"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Sin selección", "Por favor seleccione una notificación")
            return
        
        item = self.tree.item(selection[0])
        values = item['values']
        
        details = f"""
        Cupón: {values[0]}
        Tienda: {values[1]}
        URL: {values[2]}
        Fecha: {values[3]}
        """
        
        messagebox.showinfo("Detalles de la Notificación", details)
    
    def open_url(self):
        """Abre la URL en el navegador"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Sin selección", "Por favor seleccione una notificación")
            return
        
        item = self.tree.item(selection[0])
        url = item['values'][2]
        
        if url and url.startswith(('http://', 'https://')):
            import webbrowser
            webbrowser.open(url)
        else:
            messagebox.showwarning("URL inválida", "La URL no es válida")
    
    def clear_notifications(self):
        """Limpia todas las notificaciones"""
        if messagebox.askyesno("Confirmar", "¿Está seguro de eliminar todas las notificaciones?"):
            self.db.clear_notifications()
            self.load_notifications()
    
    def on_closing(self):
        """Maneja el cierre de la aplicación"""
        self.stop_auto_scan()
        self.db.close()
        self.root.destroy()

# ==============================================
# EJECUCIÓN PRINCIPAL
# ==============================================
if __name__ == "__main__":
    root = tk.Tk()
    app = CouponNotifierApp(root)
    
    # Configurar cierre limpio
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    root.mainloop()
