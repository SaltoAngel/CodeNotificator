import tkinter
from tkinter import ttk, messagebox
import sqlite3
import os
import time
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# 1. CONFIGURACIÓN (solo una vez)
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDENTIALS_FILE = 'credentials.json'  # Descargado de Google Cloud (una vez)
TOKEN_FILE = 'token.json'              # Se genera automáticamente (y se reusa)

def obtener_servicio_gmail():
    """Obtiene el servicio de Gmail, reusando token o pidiendo autenticación SOLO si es necesario"""
    creds = None
    
    # A. ¿Ya tenemos token válido?
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    # B. Si no hay token o expiró, refrescar o pedir nuevo
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Token expirado pero tenemos refresh token → refrescar automáticamente
            creds.refresh(Request())
        else:
            # Primera vez o sin refresh token → abrir navegador (SOLO ESTA VEZ)
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
        
        # Guardar token para futuras ejecuciones
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    
    # C. Devolver servicio listo para usar
    return build('gmail', 'v1', credentials=creds)

def verificar_credenciales():
    """Verifica las credenciales de Gmail y guarda/renueva el token.json mostrando el estado."""
    try:
        servicio = obtener_servicio_gmail()
        # Si no lanza excepción, el servicio está listo y el token está almacenado/refrescado
        messagebox.showinfo("Gmail", "Autenticación correcta. Token guardado/actualizado.")
    except FileNotFoundError:
        messagebox.showerror(
            "Gmail",
            f"No se encontró {CREDENTIALS_FILE}. Coloca el archivo credentials.json en la carpeta del proyecto."
        )
    except Exception as e:
        messagebox.showerror("Gmail", f"Error al autenticar:\n{e}")


def _obtener_header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _formatear_fecha(unix_seconds):
    return datetime.fromtimestamp(unix_seconds).strftime("%Y-%m-%d %H:%M:%S")


def obtener_timeout_segundos():
    cur = conn.execute("SELECT TmpTime FROM config WHERE id = 1")
    fila = cur.fetchone()
    return max(int(fila[0]), 1) if fila else 30


def cargar_tabla():
    # Limpia y recarga la tabla con los datos actuales
    for item in tree.get_children():
        tree.delete(item)
    cur = conn.execute("SELECT cupon, tienda, URL, Fecha FROM notifications ORDER BY Fecha DESC")
    for row in cur.fetchall():
        tree.insert("", "end", values=row)


def fetch_emails_desde_ultimo():
    """Lee correos nuevos desde el último timestamp guardado y los inserta en notifications."""
    asegurar_config_por_defecto()
    servicio = obtener_servicio_gmail()

    cfg = conn.execute("SELECT COALESCE(LastMessageTs, 0) FROM config WHERE id = 1").fetchone()
    last_ts = cfg[0] if cfg else 0

    query = f"after:{last_ts}" if last_ts else None
    resp = servicio.users().messages().list(userId='me', q=query, maxResults=20).execute()
    mensajes = resp.get('messages', [])

    if not mensajes:
        return

    nuevos = []
    for msg in reversed(mensajes):  # procesar de antiguo a nuevo
        det = servicio.users().messages().get(
            userId='me', id=msg['id'], format='metadata',
            metadataHeaders=['Subject', 'From']
        ).execute()

        internal_ts = int(det.get('internalDate', '0')) // 1000
        if internal_ts <= last_ts:
            continue

        subject = _obtener_header(det.get('payload', {}).get('headers', []), 'Subject') or "(Sin asunto)"
        remitente = _obtener_header(det.get('payload', {}).get('headers', []), 'From') or ""
        url = f"https://mail.google.com/mail/u/0/#inbox/{det['id']}"
        fecha_txt = _formatear_fecha(internal_ts)

        conn.execute(
            "INSERT INTO notifications (cupon, tienda, URL, Fecha) VALUES (?, ?, ?, ?)",
            (subject, remitente, url, fecha_txt)
        )
        nuevos.append((internal_ts, det['id']))

    if nuevos:
        nuevo_ts = max(ts for ts, _ in nuevos)
        ultimo_id = max(nuevos, key=lambda x: x[0])[1]
        conn.execute("UPDATE config SET LastMessageTs = ?, LastMessageId = ? WHERE id = 1", (nuevo_ts, ultimo_id))
        conn.commit()
        cargar_tabla()


def ciclo_poll():
    """Ejecuta fetch y reprograma según TmpTime (segundos)."""
    try:
        fetch_emails_desde_ultimo()
    except Exception as e:
        # Evita romper el loop de Tk; muestra y sigue
        messagebox.showerror("Gmail", f"Error al leer correos:\n{e}")
    timeout_ms = obtener_timeout_segundos() * 1000
    root.after(timeout_ms, ciclo_poll)
# Conecta (o crea) la base de datos SQLite que vive junto a este script.
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), 'notifications.db'))
c = conn.cursor()
# Crea una tabla para almacenar notificaciones si no existe ya.
c.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            cupon TEXT NOT NULL, 
            tienda TEXT NOT NULL,
            URL TEXT NOT NULL, 
            Fecha DATETIME DEFAULT CURRENT_TIMESTAMP)''')
c.execute('''
    CREATE TABLE IF NOT EXISTS config (
        id INTEGER PRIMARY KEY,
        TmpTime INTEGER DEFAULT 30,
        LastMessageId TEXT,
        LastMessageTs INTEGER DEFAULT 0)
        
        ''')
conn.commit()

def asegurar_columnas_config():
    """Garantiza que las columnas nuevas existan (migración segura)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(config)")}
    if "LastMessageId" not in cols:
        try:
            conn.execute("ALTER TABLE config ADD COLUMN LastMessageId TEXT")
        except sqlite3.OperationalError:
            pass
    if "LastMessageTs" not in cols:
        try:
            conn.execute("ALTER TABLE config ADD COLUMN LastMessageTs INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def asegurar_config_por_defecto():
    """Garantiza que exista un registro de configuración con id=1."""
    asegurar_columnas_config()
    cur = conn.execute("SELECT COUNT(*) FROM config WHERE id = 1")
    if cur.fetchone()[0] == 0:
        conn.execute("INSERT INTO config (id, TmpTime, LastMessageId, LastMessageTs) VALUES (1, 30, NULL, 0)")
        conn.commit()


def abrir_configuracion():
    """Abre una ventana para editar el timeout (TmpTime) del registro 1 de config."""
    asegurar_config_por_defecto()
    cur = conn.execute("SELECT TmpTime FROM config WHERE id = 1")
    valor_actual = cur.fetchone()[0]

    win = tkinter.Toplevel(root)
    win.title("Configuración")
    win.resizable(False, False)

    ttk.Label(win, text="Timeout (segundos)").grid(row=0, column=0, padx=10, pady=10, sticky="w")
    entry_timeout = ttk.Entry(win, width=10)
    entry_timeout.insert(0, str(valor_actual))
    entry_timeout.grid(row=0, column=1, padx=10, pady=10)

    def guardar():
        try:
            nuevo = int(entry_timeout.get())
            conn.execute("UPDATE config SET TmpTime = ? WHERE id = 1", (nuevo,))
            conn.commit()
            messagebox.showinfo("Configuración", "Timeout actualizado.")
            win.destroy()
        except ValueError:
            messagebox.showerror("Configuración", "Ingresa un número entero.")
        except Exception as e:
            messagebox.showerror("Configuración", f"No se pudo guardar: {e}")

    ttk.Button(win, text="Guardar", command=guardar).grid(row=1, column=0, columnspan=2, pady=(0, 10))

# Crea la ventana principal de Tkinter.
root = tkinter.Tk()
root.title("Notificador de Cupones de Descuento(Gmail)")

asegurar_config_por_defecto()

navbar = tkinter.Frame(root, bg="lightgrey", height=30)
navbar.pack(side="top", fill="x")

btns = [("Configuración", abrir_configuracion)]

for text, cmd in btns:
    tkinter.Button(navbar, text=text, command=cmd).pack(side="left", padx=5, pady=5)

# Layout principal: sidebar + contenido
main_frame = ttk.Frame(root)
main_frame.pack(fill="both", expand=True)

sidebar = ttk.Frame(main_frame, width=160, padding=(10, 10))
sidebar.pack(side="left", fill="y")

ttk.Label(sidebar, text="Gmail").pack(anchor="w", pady=(0, 6))
ttk.Button(sidebar, text="Verificar Gmail", command=verificar_credenciales).pack(fill="x")

contenido = ttk.Frame(main_frame, padding=(10, 10, 10, 10))
contenido.pack(side="left", fill="both", expand=True)

columnas = ("Cupon", "Tienda", "URL", "Fecha","Acciones")
tree = ttk.Treeview(contenido, columns=columnas, show="headings")

for col in columnas:
    tree.heading(col, text=col)
    tree.column(col, anchor="center")

scrollbar = ttk.Scrollbar(contenido, orient="vertical", command=tree.yview)
tree.configure(yscrollcommand=scrollbar.set)

# Layout
tree.grid(row=0, column=0, sticky="nsew")
scrollbar.grid(row=0, column=1, sticky="ns")
contenido.rowconfigure(0, weight=1)
contenido.columnconfigure(0, weight=1)

cur = conn.execute("SELECT cupon, tienda, URL, Fecha FROM notifications ORDER BY Fecha DESC")
for row in cur.fetchall():
    tree.insert("", "end", values=row)
# Inicia el bucle de eventos para mantener la ventana abierta.
ciclo_poll()
root.mainloop()