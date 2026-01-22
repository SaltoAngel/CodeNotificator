import tkinter
from tkinter import ttk
import os
import sqlite3
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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
        GmailKey TEXT)
        
        ''')
conn.commit()

# Crea la ventana principal de Tkinter.
root = tkinter.Tk()
root.title("Notificador de Cupones de Descuento(Gmail)")

navbar = tkinter.Frame(root, bg="lightgrey", height=30)
navbar.pack(side="top", fill="x")

btns = ["Configuración"]

for btn_text in btns:
    btn = tkinter.Button(navbar, text=btn_text)
    btn.pack(side="left", padx=5, pady=5)

contenido = ttk.Frame(root, padding=(10, 10, 10, 10))
contenido.pack(fill="both", expand=True)

columnas = ("Cupon", "Tienda", "URL", "Fecha")
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
root.mainloop()