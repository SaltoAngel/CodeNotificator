import os
import queue
import threading
import logging
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import webbrowser
from datetime import datetime, timedelta

try:
    import pystray
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

try:
    from PIL import Image
except ImportError:
    Image = None

logger = logging.getLogger('CouponNotifier')


class ToastNotification(ctk.CTkFrame):
    def __init__(self, parent, message, color="#2ECC71", duration=3000):
        super().__init__(parent, fg_color=color, corner_radius=10)
        self.label = ctk.CTkLabel(self, text=message, text_color="white", 
                                  font=ctk.CTkFont(size=13, weight="bold"), padx=20, pady=10)
        self.label.pack()
        
        # Posicionamiento (abajo a la derecha)
        self.place(relx=0.95, rely=0.92, anchor="se")
        
        # Alzar para que esté sobre otros elementos
        self.lift()
        
        # Auto-destrucción
        self.after(duration, self.destroy)


class CouponNotifierApp:
    def __init__(self, root, db, learning_system, processor, notifier):
        self.root = root
        self.root.title("CodeNotificator PRO")
        self.root.geometry("1100x750")

        # Configuración de colores y estilo
        self.setup_styles()

        self.db = db
        self.learning_system = learning_system
        self.processor = processor
        self.notifier = notifier

        self.queue = queue.Queue()
        self.scanning = False
        self.selected_cupon_id = None
        self.tray_icon = None
        self.tray_thread = None

        self.setup_ui()
        self.load_notifications()
        self.check_queue()
        self.check_configuration()
        self.check_expiration() # Nueva tarea de auto-limpieza
        self.setup_tray()
        self.bind_hotkeys()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        logger.info("Interfaz modernizada iniciada")

    def show_toast(self, message, color="#3498DB"):
        """Muestra una notificación flotante no bloqueante."""
        ToastNotification(self.root, message, color)

    def setup_styles(self):
        # Estilo para el Treeview (Tkinter estándar pero con colores oscuros)
        style = ttk.Style()
        style.theme_use("default")
        
        # Colores personalizados para modo oscuro
        bg_color = "#2b2b2b"
        fg_color = "#ffffff"
        selected_color = "#1f538d"
        header_color = "#333333"

        style.configure("Treeview",
                        background=bg_color,
                        foreground=fg_color,
                        fieldbackground=bg_color,
                        rowheight=30,
                        borderwidth=0,
                        font=("Segoe UI", 10))
        
        style.map("Treeview", background=[('selected', selected_color)])
        
        style.configure("Treeview.Heading",
                        background=header_color,
                        foreground=fg_color,
                        relief="flat",
                        font=("Segoe UI", 10, "bold"))
        
        style.map("Treeview.Heading", background=[('active', "#444444")])

    def setup_ui(self):
        # Sidebar/Toolbar lateral (Opcional, pero para este diseño usaremos Toolbar superior moderna)
        self.main_container = ctk.CTkFrame(self.root, corner_radius=0)
        self.main_container.pack(fill="both", expand=True)

        # 1. Header
        self.header_frame = ctk.CTkFrame(self.main_container, height=60, corner_radius=0, fg_color="#1f538d")
        self.header_frame.pack(fill="x", padx=0, pady=0)
        
        self.title_label = ctk.CTkLabel(self.header_frame, text="🚀 CODENOTIFICATOR PRO", 
                                        font=ctk.CTkFont(size=20, weight="bold"))
        self.title_label.pack(pady=15)

        # 2. Toolbar superior
        self.toolbar = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.toolbar.pack(fill="x", padx=15, pady=10)

        # Botones principales con iconos e identificadores claros
        self.btn_scan = ctk.CTkButton(self.toolbar, text="🔍 ESCANEAR", command=self.start_scan,
                                      fg_color="#27AE60", hover_color="#219150", width=120)
        self.btn_scan.pack(side="left", padx=5)

        self.btn_config = ctk.CTkButton(self.toolbar, text="⚙️ CONFIG", command=self.open_config, width=100)
        self.btn_config.pack(side="left", padx=5)

        self.btn_stats = ctk.CTkButton(self.toolbar, text="📊 STATS", command=self.show_stats, width=100)
        self.btn_stats.pack(side="left", padx=5)

        self.btn_dict = ctk.CTkButton(self.toolbar, text="📚 REGLAS", command=self.open_dictionary_manager, width=100)
        self.btn_dict.pack(side="left", padx=5)

        self.btn_export = ctk.CTkButton(self.toolbar, text="📥 EXPORTAR", command=self.export_to_csv, width=100)
        self.btn_export.pack(side="left", padx=5)

        # Espaciador
        ctk.CTkLabel(self.toolbar, text="", width=20).pack(side="left", expand=True)

        self.btn_clear = ctk.CTkButton(self.toolbar, text="🗑️ LIMPIAR", command=self.clear_old_coupons,
                                       fg_color="#C0392B", hover_color="#962D22", width=100)
        self.btn_clear.pack(side="right", padx=5)

        # 3. Filtros
        self.filter_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.filter_frame.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(self.filter_frame, text="Filtrar por:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5)
        
        self.filter_var = ctk.StringVar(value="Todos")
        self.filter_menu = ctk.CTkOptionMenu(self.filter_frame, 
                                             values=["Todos", "Recientes (7 días)", "Alta confianza (>=80%)", "Por tienda"],
                                             variable=self.filter_var,
                                             command=lambda x: self.apply_filters())
        self.filter_menu.pack(side="left", padx=5)

        self.store_filter_var = ctk.StringVar()
        self.store_filter_entry = ctk.CTkEntry(self.filter_frame, placeholder_text="Buscar tienda o código...",
                                                textvariable=self.store_filter_var, width=250)
        self.store_filter_entry.pack(side="left", padx=5)
        self.store_filter_entry.bind("<KeyRelease>", lambda e: self.apply_filters())
        
        # El botón aplicar se queda como soporte pero ya no es estrictamente necesario
        self.btn_apply = ctk.CTkButton(self.filter_frame, text="Refrescar", width=80, command=self.apply_filters)
        self.btn_apply.pack(side="left", padx=5)

        # 4. Progress Bar (Oculta por defecto)
        self.progress_bar = ctk.CTkProgressBar(self.main_container, orientation="horizontal", mode="indeterminate")
        self.progress_bar.pack(fill="x", padx=20, pady=5)
        self.progress_bar.set(0)
        self.progress_bar.pack_forget()

        # 5. Feedback Panel (Mejorado visualmente)
        self.feedback_frame = ctk.CTkFrame(self.main_container, fg_color="#1a1a1a", height=50)
        self.feedback_frame.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(self.feedback_frame, text="¿Este cupón funcionó?", font=ctk.CTkFont(size=13)).pack(side="left", padx=20, pady=10)
        
        self.valid_btn = ctk.CTkButton(self.feedback_frame, text="✅ SÍ / CORREGIR", command=self.mark_as_valid,
                                       fg_color="#27AE60", width=140, state="disabled")
        self.valid_btn.pack(side="left", padx=10)

        self.invalid_btn = ctk.CTkButton(self.feedback_frame, text="❌ DESCARTAR", command=self.mark_as_invalid,
                                         fg_color="#C0392B", width=120, state="disabled")
        self.invalid_btn.pack(side="left", padx=10)

        self.expired_btn = ctk.CTkButton(self.feedback_frame, text="🕒 EXPIRADO", command=self.mark_as_expired,
                                         fg_color="#7f8c8d", width=120, state="disabled")
        self.expired_btn.pack(side="left", padx=10)
        
        # Acciones rápidas a la derecha
        self.btn_copy = ctk.CTkButton(self.feedback_frame, text="📋 COPIAR", command=self.copy_selection, width=100)
        self.btn_copy.pack(side="right", padx=10)
        
        self.btn_go = ctk.CTkButton(self.feedback_frame, text="🔗 IR A WEB", command=self.copy_and_open_selected, width=100)
        self.btn_go.pack(side="right", padx=10)

        # 6. Pestañas y Tablas
        self.tabview = ctk.CTkTabview(self.main_container)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.tab_pending = self.tabview.add("Pendientes")
        self.tab_validated = self.tabview.add("Validados")
        self.tab_false = self.tabview.add("Falsos Positivos")
        self.tab_dashboard = self.tabview.add("📊 Dashboard")
        
        # Vincular cambio de pestaña para manejar el foco
        self.tabview.configure(command=self.on_tab_changed)
        
        self.setup_dashboard(self.tab_dashboard)

        self.trees = {}
        self.trees["pending"] = self.create_modern_tree(self.tab_pending)
        self.trees["validated"] = self.create_modern_tree(self.tab_validated)
        self.trees["false"] = self.create_modern_tree(self.tab_false)

        # 7. Status Bar
        self.status_frame = ctk.CTkFrame(self.main_container, height=30, corner_radius=0, fg_color="#111111")
        self.status_frame.pack(fill="x", side="bottom")
        
        self.count_label = ctk.CTkLabel(self.status_frame, text="0 cupones", font=ctk.CTkFont(size=11))
        self.count_label.pack(side="left", padx=20)

        self.stats_label = ctk.CTkLabel(self.status_frame, text="Cerebro ML: Activo", font=ctk.CTkFont(size=11), text_color="#7f8c8d")
        self.stats_label.pack(side="right", padx=20)

        # Menú contextual
        self.context_menu = tk.Menu(self.root, tearoff=0, bg="#333333", fg="white", activebackground="#1f538d")
        self.context_menu.add_command(label="🔗 Ir a la Web", command=self.open_selected_url)
        self.context_menu.add_command(label="📋 Copiar Código", command=self.copy_selected_code)
        self.context_menu.add_command(label="📧 Ver Correo Original", command=self.open_selected_email)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="✅ SÍ / CORREGIR", command=self.mark_as_valid)
        self.context_menu.add_command(label="❌ DESCARTAR / BASURA", command=self.mark_as_invalid)
        self.context_menu.add_command(label="🕒 EXPIRADO", command=self.mark_as_expired)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑️ Eliminar", command=self.delete_selected_coupon)

    def create_modern_tree(self, parent):
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="both", expand=True)

        columns = ("N°", "Cupón", "Tienda", "Asunto", "Descuento", "URL", "Confianza", "Fecha", "Expira")
        tree = ttk.Treeview(container, columns=columns, show="headings", style="Treeview")

        col_widths = [40, 130, 120, 200, 80, 150, 80, 140, 140]
        for col, width in zip(columns, col_widths):
            tree.heading(col, text=col)
            tree.column(col, width=width, anchor="center")

        # Scrollbars modernos
        vsb = ctk.CTkScrollbar(container, orientation="vertical", command=tree.yview)
        hsb = ctk.CTkScrollbar(container, orientation="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        # Tags para colores de confianza
        tree.tag_configure("conf_high", foreground="#2ECC71")
        tree.tag_configure("conf_mid", foreground="#F1C40F")
        tree.tag_configure("conf_low", foreground="#E74C3C")
        tree.tag_configure("flag_false", foreground="#95a5a6")
        tree.tag_configure("flag_positive", foreground="#3498DB")

        tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        tree.bind("<Double-Button-1>", self.show_details)
        tree.bind("<Button-3>", self.on_tree_right_click)
        
        # Soporte para navegación con flechas
        tree.bind("<Up>", lambda e: self.on_tree_select(e))
        tree.bind("<Down>", lambda e: self.on_tree_select(e))

        return tree

    # --- Lógica de la bandeja del sistema ---
    def setup_tray(self):
        if not TRAY_AVAILABLE or Image is None:
            return

        try:
            icon_image = self._create_tray_icon()
            menu = pystray.Menu(
                pystray.MenuItem("Mostrar CodeNotificator", self._tray_show_window),
                pystray.MenuItem("Escanear ahora", self._tray_scan_now),
                pystray.MenuItem("Salir", self._tray_quit_app)
            )

            self.tray_icon = pystray.Icon("CodeNotificator", icon_image, "CodeNotificator PRO", menu)
            self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            self.tray_thread.start()
        except Exception as e:
            logger.error(f"Error en bandeja: {e}")

    def _create_tray_icon(self):
        try:
            icon_path = os.path.join(os.path.dirname(__file__), '..', 'icon.ico')
            if os.path.exists(icon_path):
                return Image.open(icon_path)
        except Exception:
            pass
        return Image.new('RGB', (64, 64), color=(31, 83, 141))

    # --- Eventos de UI ---
    def on_tree_select(self, event=None):
        if event:
            tree = event.widget
            self.current_tree = tree
        else:
            tree = self.current_tree
            
        if not tree: return
        
        selection = tree.selection()
        if selection:
            self.selected_cupon_id = int(selection[0])
            self.valid_btn.configure(state="normal")
            self.invalid_btn.configure(state="normal")
            self.expired_btn.configure(state="normal")
        else:
            self.selected_cupon_id = None
            self.valid_btn.configure(state="disabled")
            self.invalid_btn.configure(state="disabled")
            self.expired_btn.configure(state="disabled")

    def on_tree_right_click(self, event):
        tree = event.widget
        self.current_tree = tree
        item = tree.identify_row(event.y)
        if item:
            tree.selection_set(item)
            tree.focus(item)
            self.selected_cupon_id = int(item)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    # --- Acciones de Datos ---
    def load_notifications(self):
        for tree in self.trees.values():
            for item in tree.get_children():
                tree.delete(item)

        filter_type = self.filter_var.get()
        days = 7 if filter_type == "Recientes (7 días)" else None
        min_conf = 0.8 if filter_type == "Alta confianza (>=80%)" else None
        
        # El cuadro de búsqueda siempre debe filtrar si tiene texto
        search_query = self.store_filter_var.get().strip()
        store = search_query if search_query else None

        pending = self.db.get_notifications(limit=200, min_confidence=min_conf, days=days, tienda=store, usuario_valido=False)
        validated = self.db.get_notifications(limit=200, min_confidence=min_conf, days=days, tienda=store, usuario_valido=True, es_valido=True)
        false_pos = self.db.get_notifications(limit=200, min_confidence=min_conf, days=days, tienda=store, usuario_valido=True, es_valido=False)

        self._fill_tree(self.trees["pending"], pending)
        self._fill_tree(self.trees["validated"], validated)
        self._fill_tree(self.trees["false"], false_pos)

        total = len(pending) + len(validated) + len(false_pos)
        self.count_label.configure(text=f"Total: {total} cupones encontrados")
        self.update_stats_display()

    def _fill_tree(self, tree, rows):
        for idx, row in enumerate(rows, 1):
            # Acceso por nombre de columna (row ya es un dict desde db_manager)
            cupon_id = row['id']
            code = row['cupon']
            tienda = row['tienda']
            subject = row.get('asunto', "")
            url = row['URL']
            desc = row.get('descuento', "")
            status = row.get('estado', "")
            usr_val = row.get('usuario_valido', 0)
            is_val = row.get('es_valido', 0)
            conf = row.get('confianza', 0.5)
            date = row.get('Fecha', "")
            expira = row.get('fecha_expiracion', "")
            
            tag = "conf_mid"
            if self.db.is_false_positive_term(code) or (usr_val and not is_val) or status == 'expirado':
                tag = "flag_false"
            elif self.db.is_positive_coupon(code, tienda) or (usr_val and is_val):
                tag = "flag_positive"
            elif conf >= 0.8: tag = "conf_high"
            elif conf < 0.5: tag = "conf_low"

            tree.insert("", "end", iid=str(cupon_id), values=(
                idx, code, tienda, subject, desc, url, f"{conf:.0%}", date, expira or "---"
            ), tags=(tag,))

    def bind_hotkeys(self):
        """Vincula teclas de acceso rápido para acciones frecuentes desde la configuración."""
        try:
            config = self.db.get_config()
            k_valid = config.get('key_valid', 'v').lower()
            k_discard = config.get('key_discard', 'x').lower()
            k_expired = config.get('key_expired', 'e').lower()
            k_scan = config.get('key_scan', 's').lower()

            # Desvincular teclas anteriores para evitar duplicados si se llama varias veces (aunque tkinter suele sobrescribir)
            self.root.bind(f"<Key-{k_valid}>", lambda e: self.mark_as_valid())
            self.root.bind(f"<Key-{k_valid.upper()}>", lambda e: self.mark_as_valid())
            self.root.bind(f"<Key-{k_discard}>", lambda e: self.mark_as_invalid())
            self.root.bind(f"<Key-{k_discard.upper()}>", lambda e: self.mark_as_invalid())
            self.root.bind("<Delete>", lambda e: self.mark_as_invalid())
            self.root.bind(f"<Key-{k_expired}>", lambda e: self.mark_as_expired())
            self.root.bind(f"<Key-{k_expired.upper()}>", lambda e: self.mark_as_expired())
            self.root.bind(f"<Key-{k_scan}>", lambda e: self.start_scan())
            self.root.bind(f"<Key-{k_scan.upper()}>", lambda e: self.start_scan())
            
            # Hotkeys fijas (no configurables por ahora para evitar conflictos de navegación)
            self.root.bind("<Key-c>", lambda e: self.copy_selection())
            self.root.bind("<Key-C>", lambda e: self.copy_selection())
            self.root.bind("<Return>", lambda e: self.show_details(e))

            # Hotkeys de Navegación de Pestañas
            self.root.bind("<Control-Key-1>", lambda e: self.change_tab("Pendientes"))
            self.root.bind("<Control-Key-2>", lambda e: self.change_tab("Validados"))
            self.root.bind("<Control-Key-3>", lambda e: self.change_tab("Falsos Positivos"))
            self.root.bind("<Control-Key-4>", lambda e: self.change_tab("📊 Dashboard"))
        except Exception as e:
            logger.error(f"Error vinculando hotkeys: {e}")

    def change_tab(self, name):
        """Cambia de pestaña por nombre y gestiona el foco."""
        self.tabview.set(name)
        self.on_tab_changed()

    def on_tab_changed(self):
        """Gestiona el foco cuando cambia la pestaña activa."""
        tab = self.tabview.get()
        mapping = {
            "Pendientes": "pending",
            "Validados": "validated",
            "Falsos Positivos": "false"
        }
        
        if tab in mapping:
            tree = self.trees[mapping[tab]]
            self.current_tree = tree
            tree.focus_set()
            # Seleccionar el primer elemento si no hay selección
            if not tree.selection() and tree.get_children():
                first = tree.get_children()[0]
                tree.selection_set(first)
                tree.focus(first)
                self.on_tree_select(None)

    def check_expiration(self):
        """Tarea periódica para limpiar cupones vencidos cada 15 minutos."""
        count = self.db.mark_expired_coupons()
        if count > 0:
            self.load_notifications()
        self.root.after(15 * 60 * 1000, self.check_expiration)

    def start_scan(self):
        if self.scanning:
            return
        self.scanning = True
        self.btn_scan.configure(state="disabled", text="⏳ ESCANEANDO")
        self.progress_bar.pack(fill="x", padx=20, pady=5)
        self.progress_bar.start()
        
        threading.Thread(target=self.perform_scan, daemon=True).start()

    def perform_scan(self):
        try:
            coupons, msg, err = self.processor.scan_emails()
            self.queue.put(("scan_complete", (coupons, msg, err)))
        except Exception as e:
            self.queue.put(("scan_complete", ([], str(e), "unknown")))

    def check_queue(self):
        try:
            while True:
                msg_type, data = self.queue.get_nowait()
                if msg_type == "scan_complete":
                    coupons, msg, err = data
                    self.scanning = False
                    self.btn_scan.configure(state="normal", text="🔍 ESCANEAR")
                    self.progress_bar.stop()
                    self.progress_bar.pack_forget()
                    self.load_notifications()
                    
                    if err:
                        messagebox.showerror("Error de Escaneo", f"Ocurrió un problema: {msg}")
                    elif coupons:
                        self.notifier.notify_new_coupons(len(coupons), coupons)
                        messagebox.showinfo("¡Éxito!", f"Se encontraron {len(coupons)} cupones nuevos.")
        except queue.Empty:
            pass
        self.root.after(100, self.check_queue)

    # --- Windows Auxiliares ---
    def open_config(self):
        win = ctk.CTkToplevel(self.root)
        win.title("Configuración Global")
        win.geometry("500x550")
        win.after(100, lambda: [win.lift(), win.focus_force()])
        win.grab_set()

        tabs = ctk.CTkTabview(win)
        tabs.pack(fill="both", expand=True, padx=20, pady=20)
        
        t_gen = tabs.add("General")
        t_gmail = tabs.add("Gmail API")
        t_keys = tabs.add("Atajos")

        # General Tab
        config = self.db.get_config()
        
        # Intervalo
        ctk.CTkLabel(t_gen, text="Intervalo de escaneo automático:", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        interval_var = ctk.StringVar(value=str(config.get('intervalo_minutos', 30)))
        ctk.CTkEntry(t_gen, textvariable=interval_var, width=150).pack(pady=5)
        ctk.CTkLabel(t_gen, text="minutos", font=ctk.CTkFont(size=12)).pack(pady=(0, 15))
        
        # Límite de Correos
        ctk.CTkLabel(t_gen, text="Límite de correos por escaneo:", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        limit_var = ctk.StringVar(value=str(config.get('max_emails', 50)))
        ctk.CTkEntry(t_gen, textvariable=limit_var, width=150).pack(pady=5)
        ctk.CTkLabel(t_gen, text="Cantidad de emails a revisar", font=ctk.CTkFont(size=11), text_color="gray").pack()

        # Respaldo de Inteligencia
        ctk.CTkLabel(t_gen, text="Portabilidad de Datos (Cerebro IA):", font=ctk.CTkFont(weight="bold")).pack(pady=(25, 5))
        f_backup = ctk.CTkFrame(t_gen, fg_color="transparent")
        f_backup.pack(pady=5)
        
        ctk.CTkButton(f_backup, text="📦 EXPORTAR CEREBRO", width=160, 
                       command=self.export_brain, fg_color="#3498DB").pack(side="left", padx=5)
        ctk.CTkButton(f_backup, text="📥 IMPORTAR CEREBRO", width=160, 
                       command=self.import_brain, fg_color="#E67E22").pack(side="left", padx=5)
        ctk.CTkLabel(t_gen, text="Exporta/Importa tus reglas, tiendas y bloqueos.", font=ctk.CTkFont(size=11), text_color="gray").pack()

        # Gmail Tab
        ctk.CTkLabel(t_gmail, text="Estado de Conexión", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=20)
        email = config.get('user_email', "No conectado")
        status_color = "#2ECC71" if email != "No conectado" else "#E74C3C"
        
        ctk.CTkLabel(t_gmail, text=f"📧 {email}", text_color=status_color).pack(pady=10)
        
        # Atajos Tab
        ctk.CTkLabel(t_keys, text="Configurar Atajos de Teclado", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=20)
        
        def create_key_entry(parent, label, default_val):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", padx=40, pady=5)
            ctk.CTkLabel(f, text=label, width=150, anchor="w").pack(side="left")
            var = ctk.StringVar(value=default_val)
            entry = ctk.CTkEntry(f, textvariable=var, width=50, justify="center")
            entry.pack(side="right")
            return var

        k_valid_var = create_key_entry(t_keys, "Si / Corregir:", config.get('key_valid', 'v'))
        k_discard_var = create_key_entry(t_keys, "Descartar / Basura:", config.get('key_discard', 'x'))
        k_expired_var = create_key_entry(t_keys, "Cupón Expirado:", config.get('key_expired', 'e'))
        k_scan_var = create_key_entry(t_keys, "Iniciar Escaneo:", config.get('key_scan', 's'))
        
        ctk.CTkLabel(t_keys, text="Nota: Introduce solo una letra (ej: 'v', 'x', 'e').", 
                      font=ctk.CTkFont(size=11), text_color="gray").pack(pady=10)

        def run_auth():
            try:
                from core.gmail_engine import GmailAuthenticator
                tokens = GmailAuthenticator.obtener_tokens_interactivo()
                if tokens:
                    self.db.update_config(**tokens)
                    messagebox.showinfo("Éxito", "Cuenta vinculada correctamente.")
                    win.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        ctk.CTkButton(t_gmail, text="VINCULAR CUENTA NUEVA", command=run_auth).pack(pady=30)

        def save():
            try:
                # Validar que los atajos tengan solo 1 carácter
                keys = [k_valid_var.get(), k_discard_var.get(), k_expired_var.get(), k_scan_var.get()]
                if any(len(k.strip()) != 1 for k in keys):
                    messagebox.showerror("Error", "Cada atajo debe ser una única letra.")
                    return

                self.db.update_config(
                    intervalo_minutos=int(interval_var.get()),
                    max_emails=int(limit_var.get()),
                    key_valid=k_valid_var.get().strip().lower(),
                    key_discard=k_discard_var.get().strip().lower(),
                    key_expired=k_expired_var.get().strip().lower(),
                    key_scan=k_scan_var.get().strip().lower()
                )
                
                # Re-vincular hotkeys inmediatamente
                self.bind_hotkeys()
                
                messagebox.showinfo("Éxito", "Configuración y atajos guardados.")
                win.destroy()
            except ValueError:
                messagebox.showerror("Error", "Los valores deben ser numéricos.")

        btn_save = ctk.CTkButton(win, text="GUARDAR CAMBIOS", command=save, fg_color="#2ECC71")
        btn_save.pack(side="bottom", pady=20)

    def show_stats(self):
        stats = self.learning_system.get_stats()
        win = ctk.CTkToplevel(self.root)
        win.title("Estadísticas de Inteligencia")
        win.geometry("450x400")
        win.after(100, lambda: [win.lift(), win.focus_force()])
        
        ctk.CTkLabel(win, text="📊 RENDIMIENTO DEL CEREBRO", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=20)
        
        frame = ctk.CTkFrame(win)
        frame.pack(fill="both", expand=True, padx=30, pady=10)

        items = [
            ("Feedback Total", str(stats['total_feedback'])),
            ("Ejemplos Válidos", str(stats['valid_feedback'])),
            ("Tiendas Analizadas", str(stats['stores_learned'])),
            ("Patrones Identificados", str(stats['total_patterns'])),
            ("Confianza Promedio", f"{stats['avg_confidence']:.1%}")
        ]

        for label, val in items:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=5)
            ctk.CTkLabel(row, text=label).pack(side="left")
            ctk.CTkLabel(row, text=val, font=ctk.CTkFont(weight="bold")).pack(side="right")

    def open_dictionary_manager(self):
        win = ctk.CTkToplevel(self.root)
        win.title("Gestión de Reglas y Diccionarios")
        win.geometry("750x500")
        win.after(100, lambda: [win.lift(), win.focus_force()])
        
        tabs = ctk.CTkTabview(win)
        tabs.pack(fill="both", expand=True, padx=20, pady=20)
        
        t_fp = tabs.add("Falsos Positivos")
        t_pc = tabs.add("Cupones Estrella")

        def create_list_manager(parent, items_fetcher, adder, remover):
            listbox = tk.Listbox(parent, bg="#1a1a1a", fg="white", borderwidth=0, font=("Segoe UI", 10))
            listbox.pack(fill="both", expand=True, pady=10)
            
            entry = ctk.CTkEntry(parent, placeholder_text="Añadir nuevo valor...")
            entry.pack(fill="x", pady=5)
            
            btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
            btn_frame.pack(fill="x")
            
            def refresh():
                listbox.delete(0, tk.END)
                for item in items_fetcher():
                    if isinstance(item, tuple):
                        item = f"{item[0]} ({item[1]})"
                    listbox.insert(tk.END, item)
            
            def do_add():
                val = entry.get().strip()
                if val:
                    adder(val)
                    entry.delete(0, 'end')
                    refresh()
            
            def do_remove():
                sel = listbox.curselection()
                if sel:
                    remover(listbox.get(sel[0]).split(" (")[0])
                    refresh()

            ctk.CTkButton(btn_frame, text="Añadir", width=100, command=do_add).pack(side="left", padx=5)
            ctk.CTkButton(btn_frame, text="Eliminar", width=100, command=do_remove, fg_color="#C0392B").pack(side="left", padx=5)
            refresh()

        create_list_manager(t_fp, self.db.get_false_positive_terms, self.db.add_false_positive_term, self.db.remove_false_positive_term)
        create_list_manager(t_pc, self.db.get_positive_coupons, self.db.add_positive_coupon, self.db.remove_positive_coupon)

    # --- Acciones Rápidas ---
    def apply_filters(self):
        self.load_notifications()

    def mark_as_valid(self):
        if not self.selected_cupon_id: return
        
        cupon_data = self.db.get_notification_by_id(self.selected_cupon_id)
        if not cupon_data: return

        # Diálogo de edición
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Confirmar y Entrenar IA")
        dialog.geometry("450x400")
        dialog.after(100, lambda: [dialog.lift(), dialog.focus_force()])
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Refina los datos para entrenar a la IA:", font=ctk.CTkFont(weight="bold")).pack(pady=15)

        # Campos de edición
        def create_field(label, value):
            f = ctk.CTkFrame(dialog, fg_color="transparent")
            f.pack(fill="x", padx=30, pady=5)
            ctk.CTkLabel(f, text=label, width=80, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(f)
            entry.pack(side="left", fill="x", expand=True)
            entry.insert(0, str(value) if value else "")
            return entry

        # ID, Cupon, Tienda, URL, Asunto, Descuento, Contexto, GmailID...
        e_tienda = create_field("Tienda:", cupon_data['tienda'])
        e_codigo = create_field("Código:", cupon_data['cupon'])
        e_descuento = create_field("Descuento:", cupon_data.get('descuento', ""))
        
        ctk.CTkLabel(dialog, text="💡 Al guardar, la IA aprenderá estos datos para futuros correos.", 
                     font=ctk.CTkFont(size=11), text_color="#3498DB").pack(pady=10)

        def save_and_train():
            new_tienda = e_tienda.get().strip()
            new_codigo = e_codigo.get().strip()
            new_desc = e_descuento.get().strip()
            
            if not new_tienda or not new_codigo:
                messagebox.showwarning("Atención", "Tienda y Código son obligatorios.")
                return

            # 1. Actualizar aprendizaje ML con los datos REALE corregidos por el humano
            self.learning_system.learn_from_feedback(new_codigo, new_tienda, True, contexto=cupon_data.get('contexto'))
            
            # 2. Guardar datos corregidos en la DB y marcar como validado
            self.db.update_cupon_full(self.selected_cupon_id, new_codigo, new_tienda, new_desc)
            
            # 3. Refrescar UI
            self.load_notifications()
            dialog.destroy()
            self.show_toast("¡IA Entrenada con éxito! 🧠", color="#2ECC71")

        btn_save = ctk.CTkButton(dialog, text="✅ GUARDAR Y ENTRENAR", command=save_and_train, fg_color="#27AE60")
        btn_save.pack(pady=20, padx=30, fill="x")
        
        ctk.CTkButton(dialog, text="Cancelar", command=dialog.destroy, fg_color="transparent").pack()

    def copy_and_open_selected(self):
        tree = self.current_tree or self.trees["pending"]
        sel = tree.selection()
        if sel:
            vals = tree.item(sel[0], "values")
            self.root.clipboard_clear()
            self.root.clipboard_append(vals[1])
            if vals[5]: webbrowser.open(vals[5])

    def copy_selection(self):
        tree = self.current_tree or self.trees["pending"]
        sel = tree.selection()
        if sel:
            codes = [tree.item(i, "values")[1] for i in sel]
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(codes))
            self.show_toast(f"{len(codes)} códigos copiados. 📋")

    def delete_selected_coupon(self):
        if self.selected_cupon_id and messagebox.askyesno("Borrar", "¿Deseas eliminar este registro?"):
            self.db.delete_notification(self.selected_cupon_id)
            self.load_notifications()

    def open_selected_url(self, event=None): self.copy_and_open_selected()
    def copy_selected_code(self, event=None): self.copy_selection()
    
    def check_configuration(self):
        config = self.db.get_config()
        if config and not all([config.get('client_id'), config.get('client_secret'), config.get('refresh_token')]):
            if messagebox.askyesno("Configuración", "Falta configuración de Gmail. ¿Configurar ahora?"):
                self.open_config()

    def open_selected_email(self):
        if self.selected_cupon_id:
            id_gmail = self.db.obtener_id_gmail_por_db(self.selected_cupon_id)
            if id_gmail: webbrowser.open(f"https://mail.google.com/mail/u/0/#inbox/{id_gmail}")

    def show_details(self, event):
        sel = event.widget.selection()
        if sel:
            vals = event.widget.item(sel[0], "values")
            # N°, Cupón, Tienda, Asunto, Descuento, URL, Confianza, Fecha, Expira
            details = (f"Código: {vals[1]}\n"
                       f"Tienda: {vals[2]}\n"
                       f"Asunto: {vals[3]}\n"
                       f"Descuento: {vals[4]}\n"
                       f"Confianza: {vals[6]}\n"
                       f"Fecha Detección: {vals[7]}\n"
                       f"Fecha Expiración: {vals[8]}")
            messagebox.showinfo("Detalles del Cupón", details)

    def clear_old_coupons(self):
        if messagebox.askyesno("Limpieza", "¿Borrar cupones más antiguos a 30 días?"):
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            self.db.delete_old_notifications(cutoff)
            self.load_notifications()

    def update_stats_display(self):
        stats = self.learning_system.get_stats()
        self.stats_label.configure(text=f"Ejemplos Aprendidos: {stats['total_feedback']}")

    def setup_dashboard(self, parent):
        container = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=10, pady=10)
        
        # 1. Resumen General
        ctk.CTkLabel(container, text="Resumen de Ahorros", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        sum_frame = ctk.CTkFrame(container)
        sum_frame.pack(fill="x", padx=20, pady=10)
        
        stats = self.learning_system.get_stats()
        items = [
            ("Cupones Validados", str(stats['valid_feedback']), "#2ECC71"),
            ("Tiendas Detectadas", str(stats['stores_learned']), "#3498DB"),
            ("Ahorros Estimados", f"${stats['valid_feedback'] * 5}", "#F1C40F") # Estimación ficticia de $5 por cupón
        ]
        
        for i, (label, val, color) in enumerate(items):
            f = ctk.CTkFrame(sum_frame, fg_color="transparent")
            f.pack(side="left", expand=True, pady=15)
            ctk.CTkLabel(f, text=val, font=ctk.CTkFont(size=24, weight="bold"), text_color=color).pack()
            ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=12)).pack()

        # 1.1 Nivel de Inteligencia de la IA
        ctk.CTkLabel(container, text="Nivel de Inteligencia de la IA", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 5))
        ia_frame = ctk.CTkFrame(container)
        ia_frame.pack(fill="x", padx=20, pady=10)
        
        total_f = stats['total_feedback']
        avg_c = stats['avg_confidence']
        
        if total_f < 10:
            ia_level = "JUNIOR 🌱"
            ia_desc = "La IA está aprendiendo tus gustos iniciales."
            ia_color = "#E67E22"
        elif total_f < 50:
            ia_level = "SENIOR 🧠"
            ia_desc = "La IA ya reconoce patrones complejos con éxito."
            ia_color = "#3498DB"
        else:
            ia_level = "EXPERT 👑"
            ia_desc = "Máxima precisión basada en gran historial."
            ia_color = "#2ECC71"
            
        ctk.CTkLabel(ia_frame, text=ia_level, font=ctk.CTkFont(size=22, weight="bold"), text_color=ia_color).pack(pady=(10, 0))
        ctk.CTkLabel(ia_frame, text=ia_desc, font=ctk.CTkFont(size=13), text_color="gray").pack(pady=(0, 10))
        
        # Barra de progreso de entrenamiento
        prog_val = min(total_f / 100, 1.0) # 100 feedbacks para el máximo visual
        prog = ctk.CTkProgressBar(ia_frame, progress_color=ia_color)
        prog.pack(fill="x", padx=50, pady=(0, 15))
        prog.set(prog_val)

        # 2. Top Tiendas (Gráfico de barras simulado)
        ctk.CTkLabel(container, text="Top Tiendas por Actividad", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 10))
        stores_stats = self.db.get_store_stats(limit=5)
        
        if stores_stats:
            max_val = max([s[1] for s in stores_stats]) if stores_stats else 1
            for tienda, count in stores_stats:
                row = ctk.CTkFrame(container, fg_color="transparent")
                row.pack(fill="x", padx=40, pady=5)
                ctk.CTkLabel(row, text=f"{tienda} ({count})", width=150, anchor="w").pack(side="left")
                
                progress = ctk.CTkProgressBar(row, width=300)
                progress.pack(side="left", padx=10)
                progress.set(count / max_val)
        else:
            ctk.CTkLabel(container, text="No hay datos suficientes todavía", text_color="gray").pack()

    def export_to_csv(self):
        import csv
        from tkinter import filedialog
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile=f"cupones_validados_{datetime.now().strftime('%Y%m%d')}.csv"
        )
        
        if filename:
            try:
                # Obtener todos los cupones validados
                data = self.db.get_notifications(limit=1000, usuario_valido=True, es_valido=True)
                with open(filename, mode='w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["ID", "Cupón", "Tienda", "Asunto", "URL", "Descuento", "Fecha"])
                    for r in data:
                        # Extraer solo columnas relevantes (ID, Cupon, Tienda, Asunto, URL, Descuento, Fecha)
                        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[10]])
                
                messagebox.showinfo("Éxito", f"Archivo exportado correctamente:\n{filename}")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo exportar: {e}")

    def export_brain(self):
        import json
        from tkinter import filedialog
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")],
            initialfile=f"cerebro_ia_{datetime.now().strftime('%Y%m%d')}.json"
        )
        
        if filename:
            try:
                data = self.db.get_brain_data()
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                self.show_toast("🧠 Cerebro exportado con éxito.", color="#3498DB")
            except Exception as e:
                messagebox.showerror("Error", f"Error exportando cerebro: {e}")

    def import_brain(self):
        import json
        from tkinter import filedialog
        
        filename = filedialog.askopenfilename(
            filetypes=[("JSON Files", "*.json")]
        )
        
        if filename:
            if messagebox.askyesno("Importar Cerebro", "¿Deseas importar este archivo? Se fusionará con tu inteligencia actual."):
                try:
                    with open(filename, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self.db.import_brain_data(data)
                    self.show_toast("🧠 Cerebro importado con éxito.", color="#2ECC71")
                    self.load_notifications() # Refrescar si hay cambios visibles
                except Exception as e:
                    messagebox.showerror("Error", f"Error importando cerebro: {e}")

    def mark_as_invalid(self):
        """Descarta un cupón y entrena a la IA para ignorar el ruido (Acción Unificada)."""
        if not self.selected_cupon_id: return
        
        cupon_data = self.db.get_notification_by_id(self.selected_cupon_id)
        if cupon_data:
            code = cupon_data['cupon']
            tienda = cupon_data['tienda']
            
            # 1. Bloqueo Global (Blacklist dura)
            self.db.add_false_positive_term(code)
            
            # 2. Informar a la IA (ML weights) para que aprenda el patrón contextual
            self.learning_system.learn_from_feedback(code, tienda, False, contexto=cupon_data.get('contexto'))
            
            # 3. Marcar como inválido en la DB para sacarlo de pendientes
            self.db.update_cupon_validity(self.selected_cupon_id, False, 0.0)
            
            # 4. Actualizar UI
            self.load_notifications()
            self.show_toast(f"'{code}' descartado y bloqueado globalmente. 🚫", color="#E67E22")

    def mark_as_expired(self):
        """Marca un cupón como expirado/usado. Entrena a la IA diciendo que el patrón es BUENO."""
        if not self.selected_cupon_id: return
        
        cupon_data = self.db.get_notification_by_id(self.selected_cupon_id)
        if cupon_data:
            # 1. Feedback POSITIVO a la IA (el patrón es correcto, es un cupón real)
            self.learning_system.learn_from_feedback(cupon_data['cupon'], cupon_data['tienda'], True, contexto=cupon_data.get('contexto'))
            
            # 2. Marcar como inválido pero estado 'expirado' en la DB
            self.db.update_cupon_validity(self.selected_cupon_id, False, 0.5, estado='expirado')
            
            # 3. Actualizar UI
            self.load_notifications()
            self.show_toast("Marcado como expirado. Patrón aprendido como válido. 🕒✔️", color="#7f8c8d")

    def on_closing(self):
        if TRAY_AVAILABLE and self.tray_icon:
            self.root.withdraw()
        else:
            self.quit_app()

    def quit_app(self):
        if self.tray_icon: self.tray_icon.stop()
        self.db.close()
        self.root.destroy()

    def _tray_show_window(self, icon, item): self.root.after(0, self.root.deiconify)
    def _tray_scan_now(self, icon, item): self.root.after(0, self.start_scan)
    def _tray_quit_app(self, icon, item): self.root.after(0, self.quit_app)
