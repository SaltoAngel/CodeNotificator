import os
import sys

# Añadir el directorio raíz al path para evitar ModuleNotFoundError
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import queue
import threading
import logging
import platform
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import webbrowser
from datetime import datetime, timedelta
from utils.logo_manager import LogoManager
from utils.config_helper import generar_pdf_instrucciones, verificar_dependencias_ocr

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

        # Modo OCR desde configuración
        try:
            config = self.db.get_config() or {}
            self.processor.set_ocr_mode(config.get("ocr_mode", "default"))
        except Exception:
            self.processor.set_ocr_mode("default")

        self.queue = queue.Queue()
        self.scanning = False
        self.selected_cupon_id = None
        self.current_tree = None
        self.tray_icon = None
        self.tray_thread = None
        
        # Gestor de logotipos
        self.logo_manager = LogoManager()
        self.current_logo = None

        self.setup_ui()
        
        # Caché de optimización (Carga inicial)
        self.false_positive_cache = self.db.get_all_false_positives()
        
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
        
        # Detectar sistema operativo para usar fuentes apropiadas
        system = platform.system()
        if system == "Windows":
            ui_font = "Segoe UI"
        elif system == "Darwin":  # macOS
            ui_font = "SF Pro Display"
        else:  # Linux y otros
            ui_font = "DejaVu Sans"
        
        # Colores personalizados para modo oscuro "Deep Night"
        bg_color = "#1e1e2e" # Deep Navy/Night
        fg_color = "#cdd6f4" # Light gray
        selected_color = "#45475a" # Medium gray
        header_color = "#181825" # Darker background for header

        style.configure("Treeview",
                        background=bg_color,
                        foreground=fg_color,
                        fieldbackground=bg_color,
                        rowheight=32, # Un poco más de aire
                        borderwidth=0,
                        font=(ui_font, 10))
        
        style.map("Treeview", background=[('selected', selected_color)])
        
        style.configure("Treeview.Heading",
                        background=header_color,
                        foreground=fg_color,
                        relief="flat",
                        font=(ui_font, 10, "bold"))
        
        style.map("Treeview.Heading", background=[('active', "#313244")])

    def setup_ui(self):
        # 0. Layout Principal con Sidebar
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        # 1. SIDEBAR IZQUIERDA
        self.sidebar = ctk.CTkFrame(self.root, width=200, corner_radius=0, fg_color="#11111b")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(10, weight=1) # Espaciador inferior

        self.title_label = ctk.CTkLabel(self.sidebar, text="🚀 CODE\nNOTIFICATOR", 
                                        font=ctk.CTkFont(size=18, weight="bold"), text_color="#89b4fa")
        self.title_label.pack(pady=30, padx=20)

        # Botones de la Sidebar
        def create_sidebar_btn(text, command, color=None):
            btn = ctk.CTkButton(self.sidebar, text=text, command=command, 
                                 fg_color="transparent", anchor="w", height=40,
                                 hover_color="#313244", font=ctk.CTkFont(size=13))
            if color: btn.configure(text_color=color)
            btn.pack(fill="x", padx=10, pady=2)
            return btn

        self.btn_scan = create_sidebar_btn("🔍 ESCANEAR", self.start_scan, "#a6e3a1")
        self.btn_nav_pending = create_sidebar_btn("📥 PENDIENTES", lambda: self.change_tab("Pendientes"))
        self.btn_nav_valid = create_sidebar_btn("✅ VALIDADOS", lambda: self.change_tab("Validados"))
        self.btn_nav_false = create_sidebar_btn("❌ DESCARTADOS", lambda: self.change_tab("Falsos Positivos"))
        self.btn_nav_dash = create_sidebar_btn("📊 DASHBOARD", lambda: self.change_tab("📊 Dashboard"))
        
        ctk.CTkLabel(self.sidebar, text="HERRAMIENTAS", font=ctk.CTkFont(size=10, weight="bold"), 
                      text_color="gray").pack(pady=(20, 5), padx=20, anchor="w")
        
        self.btn_dict = create_sidebar_btn("📚 REGLAS", self.open_dictionary_manager)
        self.btn_config = create_sidebar_btn("⚙️ CONFIG", self.open_config)
        self.btn_help = create_sidebar_btn("❓ AYUDA PDF", self.generate_help_pdf)
        self.btn_export = create_sidebar_btn("📥 EXPORTAR", self.export_to_csv)
        
        self.btn_clear = create_sidebar_btn("🗑️ LIMPIAR", self.clear_old_coupons, "#f38ba8")

        # 1.1 Indicadores de Salud OCR (Sidebar)
        self.health_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.health_frame.pack(side="bottom", fill="x", padx=10, pady=20)
        
        self.tess_label = ctk.CTkLabel(self.health_frame, text="• Tesseract: ...", font=ctk.CTkFont(size=10), anchor="w")
        self.tess_label.pack(fill="x")
        self.vision_label = ctk.CTkLabel(self.health_frame, text="• Cloud Vision: ...", font=ctk.CTkFont(size=10), anchor="w")
        self.vision_label.pack(fill="x")
        
        self.health_frame.after(1000, self.update_ocr_health)

        # 2. CONTENEDOR PRINCIPAL
        self.main_container = ctk.CTkFrame(self.root, corner_radius=0, fg_color="#181825")
        self.main_container.grid(row=0, column=1, sticky="nsew")
        self.main_container.columnconfigure(0, weight=1)
        self.main_container.rowconfigure(2, weight=1)

        # Barra de Búsqueda y Filtros superior
        self.top_bar = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=20, pady=15)

        self.store_filter_var = ctk.StringVar()
        self.store_filter_entry = ctk.CTkEntry(self.top_bar, placeholder_text="Buscar tienda o código...",
                                                textvariable=self.store_filter_var, width=350, height=35)
        self.store_filter_entry.pack(side="left", padx=5)
        self.store_filter_entry.bind("<KeyRelease>", lambda e: self.apply_filters())

        self.filter_var = ctk.StringVar(value="Todos")
        self.filter_menu = ctk.CTkOptionMenu(self.top_bar, 
                                             values=["Todos", "Recientes (7 días)", "Alta confianza (>=80%)"],
                                             variable=self.filter_var,
                                             command=lambda x: self.apply_filters(), height=35)
        self.filter_menu.pack(side="left", padx=10)

        # Progress Bar (Ahora centrada en la parte superior)
        self.progress_bar = ctk.CTkProgressBar(self.main_container, orientation="horizontal", mode="indeterminate", height=4)
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=20)
        self.progress_bar.set(0)
        self.progress_bar.grid_forget()

        # ÁREA DE CONTENIDO (Pestañas)
        self.tabview = ctk.CTkTabview(self.main_container, fg_color="transparent")
        self.tabview.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        
        # En la sidebar ya tenemos navegación, pero mantenemos las pestañas internas como contenedores
        self.tab_pending = self.tabview.add("Pendientes")
        self.tab_validated = self.tabview.add("Validados")
        self.tab_false = self.tabview.add("Falsos Positivos")
        self.tab_dashboard = self.tabview.add("📊 Dashboard")
        
        # Ocultar cabeceras de pestañas para usar solo la Sidebar
        self.tabview._segmented_button.grid_forget()
        
        self.setup_dashboard(self.tab_dashboard)

        self.trees = {}
        self.trees["pending"] = self.create_modern_tree(self.tab_pending)
        self.trees["validated"] = self.create_modern_tree(self.tab_validated)
        self.trees["false"] = self.create_modern_tree(self.tab_false)

        # 3. PANEL DE DETALLES (FLY-OUT DERECHA)
        self.detail_panel = ctk.CTkFrame(self.root, width=300, corner_radius=0, fg_color="#1e1e2e")
        # Se inicia oculto, se muestra al seleccionar un cupón
        # self.detail_panel.grid(row=0, column=2, sticky="nsew") 

        ctk.CTkLabel(self.detail_panel, text="Detalles del Cupón", font=ctk.CTkFont(size=14, weight="bold"),
                      text_color="#cba6f7").pack(pady=20, padx=20)

        self.logo_panel = ctk.CTkLabel(self.detail_panel, text="🏢", width=80, height=80, font=ctk.CTkFont(size=40))
        self.logo_panel.pack(pady=10)

        self._create_detail_info_area()

        # Botones de Acción en el panel lateral
        self.action_frame = ctk.CTkFrame(self.detail_panel, fg_color="transparent")
        self.action_frame.pack(fill="x", padx=20, pady=20)

        self.valid_btn = ctk.CTkButton(self.action_frame, text="✅ VALIDAR / EDITAR", command=self.mark_as_valid,
                                       fg_color="#a6e3a1", text_color="#11111b", hover_color="#94e2d5")
        self.valid_btn.pack(fill="x", pady=5)

        self.invalid_btn = ctk.CTkButton(self.action_frame, text="❌ DESCARTAR", command=self.mark_as_invalid,
                                         fg_color="#f38ba8", text_color="#11111b")
        self.invalid_btn.pack(fill="x", pady=5)

        self.expired_btn = ctk.CTkButton(self.action_frame, text="🕒 MARCAR EXPIRADO", command=self.mark_as_expired,
                                         fg_color="#fab387", text_color="#11111b")
        self.expired_btn.pack(fill="x", pady=5)

        self.btn_go = ctk.CTkButton(self.action_frame, text="🔗 IR A LA WEB", command=self.copy_and_open_selected, 
                                     fg_color="#89b4fa", text_color="#11111b")
        self.btn_go.pack(fill="x", pady=(20, 5))

        # 4. Status Bar
        self.status_frame = ctk.CTkFrame(self.main_container, height=25, corner_radius=0, fg_color="#11111b")
        self.status_frame.grid(row=3, column=0, sticky="ew")
        
        self.count_label = ctk.CTkLabel(self.status_frame, text="0 cupones", font=ctk.CTkFont(size=11))
        self.count_label.pack(side="left", padx=20)

        self.stats_label = ctk.CTkLabel(self.status_frame, text="IA Activa", font=ctk.CTkFont(size=11), text_color="#89b4fa")
        self.stats_label.pack(side="right", padx=20)

        # Menú contextual
        self.context_menu = tk.Menu(self.root, tearoff=0, bg="#11111b", fg="white", activebackground="#45475a")
        self.context_menu.add_command(label="🔗 Ir a la Web", command=self.open_selected_url)
        self.context_menu.add_command(label="📋 Copiar Código", command=self.copy_selected_code)
        self.context_menu.add_command(label="📧 Ver Correo Original", command=self.open_selected_email)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="✅ VALIDAR / CORREGIR", command=self.mark_as_valid)
        self.context_menu.add_command(label="❌ DESCARTAR", command=self.mark_as_invalid)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑️ Eliminar de BD", command=self.delete_selected_coupon)

    def _create_detail_info_area(self):
        self.info_scroll = ctk.CTkScrollableFrame(self.detail_panel, fg_color="transparent", height=250)
        self.info_scroll.pack(fill="both", expand=True, padx=10)

        def add_info_row(label):
            l = ctk.CTkLabel(self.info_scroll, text=label, font=ctk.CTkFont(size=10, weight="bold"), text_color="gray")
            l.pack(anchor="w", padx=10, pady=(10, 0))
            v = ctk.CTkLabel(self.info_scroll, text="---", font=ctk.CTkFont(size=12), wraplength=240, justify="left")
            v.pack(anchor="w", padx=10)
            return v

        self.val_store = add_info_row("TIENDA")
        self.val_code = add_info_row("CÓDIGO")
        self.val_desc = add_info_row("DESCUENTO")
        self.val_conf = add_info_row("CONFIANZA IA")
        self.val_score = add_info_row("SCORE")
        self.val_date = add_info_row("DETECTADO")
        self.val_exp = add_info_row("EXPIRA")

    def create_modern_tree(self, parent):
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # Leyenda de iconos y colores
        legend = ctk.CTkFrame(container, fg_color="transparent")
        legend.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))
        ctk.CTkLabel(legend, text="Leyenda:", text_color="gray").pack(side="left", padx=(0, 8))
        ctk.CTkLabel(legend, text="🤖 Auto", text_color="#cdd6f4").pack(side="left", padx=6)
        ctk.CTkLabel(legend, text="🧠 Validado", text_color="#cdd6f4").pack(side="left", padx=6)
        ctk.CTkLabel(legend, text="💎 Metadata", text_color="#cdd6f4").pack(side="left", padx=6)
        ctk.CTkLabel(legend, text="📷 OCR", text_color="#cdd6f4").pack(side="left", padx=6)
        ctk.CTkLabel(legend, text="Alta", text_color="#2ECC71").pack(side="left", padx=10)
        ctk.CTkLabel(legend, text="Media", text_color="#F1C40F").pack(side="left", padx=6)
        ctk.CTkLabel(legend, text="Baja", text_color="#E74C3C").pack(side="left", padx=6)
        ctk.CTkLabel(legend, text="Validado prev.", text_color="#3498DB").pack(side="left", padx=6)
        ctk.CTkLabel(legend, text="Falso", text_color="#95a5a6").pack(side="left", padx=6)

        columns = ("N°", "IA", "Cupón", "Tienda", "Asunto", "Descuento", "Score", "URL", "Confianza", "Fecha", "Expira")
        tree = ttk.Treeview(container, columns=columns, show="headings", style="Treeview")

        col_widths = [40, 50, 130, 120, 200, 80, 70, 150, 80, 140, 140]
        for col, width in zip(columns, col_widths):
            tree.heading(col, text=col)
            tree.column(col, width=width, anchor="center")

        # Scrollbars modernos
        vsb = ctk.CTkScrollbar(container, orientation="vertical", command=tree.yview)
        hsb = ctk.CTkScrollbar(container, orientation="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        # Tags para colores de confianza
        tree.tag_configure("conf_high", foreground="#2ECC71")
        tree.tag_configure("conf_mid", foreground="#F1C40F")
        tree.tag_configure("conf_low", foreground="#E74C3C")
        tree.tag_configure("flag_false", foreground="#95a5a6")
        tree.tag_configure("flag_positive", foreground="#3498DB")

        tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        tree.bind("<Double-Button-1>", self.show_details)
        tree.bind("<Button-3>", self.on_tree_right_click)
        
        # Soporte para navegación con teclado avanzada
        def arrow_handler(e):
            # Si no hay selección, seleccionar el primero al bajar
            if not tree.selection() and tree.get_children():
                first = tree.get_children()[0]
                tree.selection_set(first)
                tree.focus(first)
                tree.see(first)
            
            # Dejar que el Treeview procese el movimiento y luego actualizar panel
            tree.after(10, self.on_tree_select)
            
        tree.bind("<Up>", arrow_handler)
        tree.bind("<Down>", arrow_handler)
        tree.bind("<Return>", lambda e: self.show_details())

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
            # Mostrar panel si estaba oculto
            self.detail_panel.grid(row=0, column=2, sticky="nsew")

            values = tree.item(selection[0], "values")
            self.selected_cupon_id = int(selection[0])
            
            self.valid_btn.configure(state="normal")
            self.invalid_btn.configure(state="normal")
            self.expired_btn.configure(state="normal")
            
            # Actualizar labels de información (N°, IA, Cupón, Tienda, Asunto, Descuento, Score, URL, Confianza, Fecha, Expira)
            self.val_store.configure(text=values[3])
            self.val_code.configure(text=values[2])
            self.val_desc.configure(text=values[5] or "Sin especificar")
            self.val_score.configure(text=values[6])
            self.val_conf.configure(text=f"{values[1]} {values[8]}")
            self.val_date.configure(text=values[9])
            self.val_exp.configure(text=values[10])

            # Actualizar Logo
            self.update_store_logo(values)
        else:
            # Ocultar panel si no hay selección
            self.detail_panel.grid_forget()
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
    def load_notifications(self, preserve_selection=True):
        tree_with_focus = self.current_tree
        selected_id = None
        if preserve_selection and tree_with_focus:
            selection = tree_with_focus.selection()
            if selection:
                # Buscamos el siguiente ID al que saltar si el actual va a desaparecer
                all_ids = tree_with_focus.get_children()
                current_index = all_ids.index(selection[0])
                if current_index + 1 < len(all_ids):
                    selected_id = all_ids[current_index + 1]
                elif current_index - 1 >= 0:
                    selected_id = all_ids[current_index - 1]

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

        # Restaurar o mover selección
        if preserve_selection and tree_with_focus and selected_id:
            if tree_with_focus.exists(selected_id):
                tree_with_focus.selection_set(selected_id)
                tree_with_focus.see(selected_id)
                tree_with_focus.focus(selected_id)
                self.on_tree_select()

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
            score = row.get('score', None)
            date = row.get('Fecha', "")
            expira = row.get('fecha_expiracion', "")
            
            tag = "conf_mid"
            # Optimización: Verificación local en RAM en lugar de consulta SQL
            is_blacklisted = code and code.upper() in self.false_positive_cache
            
            if is_blacklisted or (usr_val and not is_val) or status == 'expirado':
                tag = "flag_false"
            elif self.db.is_positive_coupon(code, tienda) or (usr_val and is_val):
                tag = "flag_positive"
            elif conf >= 0.8: tag = "conf_high"
            elif conf < 0.5: tag = "conf_low"

            # Selector de icono IA
            ia_icon = "🤖" # Auto-detectado
            if usr_val:
                ia_icon = "🧠" # Entrenado por humano
            elif row.get('metodo') == 'Metadata':
                ia_icon = "💎" # Directo de Gmail
            elif row.get('metodo') == 'Nube (Google)':
                ia_icon = "☁️" # Rescatado por Google Cloud Vision
            elif row.get('is_ocr'):
                ia_icon = "📷" # Vía OCR Local

            score_text = f"{float(score):.2f}" if score is not None else "---"
            tree.insert("", "end", iid=str(cupon_id), values=(
                idx, ia_icon, code, tienda, subject, desc, score_text, url, f"{conf:.0%}", date, expira or "---"
            ), tags=(tag,))

    def update_store_logo(self, values):
        """Actualiza el logo de la tienda en el panel de feedback."""
        if not Image:
            return

        # N°, IA, Cupón, Tienda, Asunto, Descuento, URL...
        store_name = values[3]
        store_url = values[6]
        
        def load():
            try:
                logo_path = self.logo_manager.get_logo_path(store_name, store_url)
                if logo_path and os.path.exists(logo_path):
                    pil_img = Image.open(logo_path).resize((40, 40), Image.LANCZOS)
                    ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(40, 40))
                    self.root.after(0, lambda: self.logo_panel.configure(image=ctk_img, text=""))
                else:
                    self.root.after(0, lambda: self.logo_panel.configure(image=None, text="🏢"))
            except Exception as e:
                logger.error(f"Error cargando logo en UI: {e}")
                self.root.after(0, lambda: self.logo_panel.configure(image=None, text="🏢"))

        # Cargar de forma asíncrona para no bloquear el hilo principal (descarga de red)
        threading.Thread(target=load, daemon=True).start()

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
        """Cambia de pestaña por nombre y gestiona el foco y realce de sidebar."""
        self.tabview.set(name)
        self.on_tab_changed()
        
        # Realce visual en Sidebar
        mapping = {
            "Pendientes": self.btn_nav_pending,
            "Validados": self.btn_nav_valid,
            "Falsos Positivos": self.btn_nav_false,
            "📊 Dashboard": self.btn_nav_dash
        }
        
        for tab_name, btn in mapping.items():
            if tab_name == name:
                btn.configure(fg_color="#313244", border_width=1, border_color="#89b4fa")
            else:
                btn.configure(fg_color="transparent", border_width=0)

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
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=20)
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
                    self.progress_bar.grid_forget()
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
        # Mover grab_set() dentro del callback para evitar errores en Linux
        win.after(100, lambda: [win.lift(), win.focus_force(), win.grab_set()])

        tabs = ctk.CTkTabview(win)
        tabs.pack(fill="both", expand=True, padx=20, pady=20)
        
        t_gen = tabs.add("General")
        t_gmail = tabs.add("Gmail API")
        t_keys = tabs.add("Atajos")
        t_info = tabs.add("Info / Cambios")

        # --- Tab Info (Changelog) ---
        try:
            with open("CHANGELOG.md", "r", encoding="utf-8") as f:
                changelog_text = f.read()
        except Exception:
            changelog_text = "No se pudo cargar el archivo CHANGELOG.md"

        info_box = ctk.CTkTextbox(t_info, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        info_box.pack(fill="both", expand=True, padx=5, pady=5)
        info_box.insert("0.0", changelog_text)
        info_box.configure(state="disabled")

        # General Tab
        config = self.db.get_config()

        gen_frame = ctk.CTkScrollableFrame(t_gen)
        gen_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Intervalo
        ctk.CTkLabel(gen_frame, text="Intervalo de escaneo automático:", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        interval_var = ctk.StringVar(value=str(config.get('intervalo_minutos', 30)))
        ctk.CTkEntry(gen_frame, textvariable=interval_var, width=150).pack(pady=5)
        ctk.CTkLabel(gen_frame, text="minutos", font=ctk.CTkFont(size=12)).pack(pady=(0, 15))
        
        # Límite de Correos
        ctk.CTkLabel(gen_frame, text="Límite de correos por escaneo:", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        limit_var = ctk.StringVar(value=str(config.get('max_emails', 50)))
        ctk.CTkEntry(gen_frame, textvariable=limit_var, width=150).pack(pady=5)
        ctk.CTkLabel(gen_frame, text="Cantidad de emails a revisar", font=ctk.CTkFont(size=11), text_color="gray").pack()

        # Modo OCR
        ctk.CTkLabel(gen_frame, text="Modo OCR:", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        ocr_mode_var = ctk.StringVar(value=config.get('ocr_mode', 'default'))
        ctk.CTkOptionMenu(
            gen_frame,
            values=["default", "tesseract", "easyocr", "google"],
            variable=ocr_mode_var
        ).pack(pady=5)
        ctk.CTkLabel(gen_frame, text="default = waterfall automático", font=ctk.CTkFont(size=11), text_color="gray").pack()

        # Cuota Cloud Vision
        ctk.CTkLabel(gen_frame, text="Cuota mensual Cloud Vision (opcional):", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        vision_quota_var = ctk.StringVar(value=str(config.get('vision_quota', 0) or 0))
        ctk.CTkEntry(gen_frame, textvariable=vision_quota_var, width=150).pack(pady=5)
        ctk.CTkLabel(gen_frame, text="0 = sin control. Se descuenta por uso de Vision.", font=ctk.CTkFont(size=11), text_color="gray").pack()

        # Umbral de score (OCR/Regex)
        ctk.CTkLabel(gen_frame, text="Umbral de score (0.10 - 0.90):", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        score_threshold_var = ctk.StringVar(value=str(config.get('score_threshold', 0.30)))
        ctk.CTkEntry(gen_frame, textvariable=score_threshold_var, width=150).pack(pady=5)
        ctk.CTkLabel(gen_frame, text="Más alto = más estricto. Recomendado: 0.30", font=ctk.CTkFont(size=11), text_color="gray").pack()

        ctk.CTkLabel(gen_frame, text="Google Vision deshabilitado temporalmente (sin billing).", font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(10, 0))

        # Respaldo de Inteligencia
        ctk.CTkLabel(gen_frame, text="Portabilidad de Datos (Cerebro IA):", font=ctk.CTkFont(weight="bold")).pack(pady=(25, 5))
        f_backup = ctk.CTkFrame(gen_frame, fg_color="transparent")
        f_backup.pack(pady=5)
        
        ctk.CTkButton(f_backup, text="📦 EXPORTAR CEREBRO", width=160, 
                       command=self.export_brain, fg_color="#3498DB").pack(side="left", padx=5)
        ctk.CTkButton(f_backup, text="📥 IMPORTAR CEREBRO", width=160, 
                       command=self.import_brain, fg_color="#E67E22").pack(side="left", padx=5)
        ctk.CTkLabel(gen_frame, text="Exporta/Importa tus reglas, tiendas y bloqueos.", font=ctk.CTkFont(size=11), text_color="gray").pack()

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

                # Validación OCR
                ocr_mode = ocr_mode_var.get().strip().lower()
                if ocr_mode not in ["default", "tesseract", "easyocr", "google"]:
                    ocr_mode = "default"

                vision_quota = int(vision_quota_var.get() or 0)
                if vision_quota < 0:
                    messagebox.showerror("Error", "La cuota de Cloud Vision no puede ser negativa.")
                    return

                score_threshold = float(score_threshold_var.get() or 0.30)
                if score_threshold < 0.10 or score_threshold > 0.90:
                    messagebox.showerror("Error", "El umbral debe estar entre 0.10 y 0.90.")
                    return

                self.db.update_config(
                    intervalo_minutos=int(interval_var.get()),
                    max_emails=int(limit_var.get()),
                    ocr_mode=ocr_mode,
                    vision_quota=vision_quota,
                    score_threshold=score_threshold,
                    key_valid=k_valid_var.get().strip().lower(),
                    key_discard=k_discard_var.get().strip().lower(),
                    key_expired=k_expired_var.get().strip().lower(),
                    key_scan=k_scan_var.get().strip().lower()
                )
                
                # Re-vincular hotkeys inmediatamente
                self.bind_hotkeys()

                # Aplicar modo OCR y refrescar salud
                self.processor.set_ocr_mode(ocr_mode)
                try:
                    self.processor.extractor.set_score_threshold(score_threshold)
                except Exception:
                    pass
                self.update_ocr_health()
                
                messagebox.showinfo("Éxito", "Configuración y atajos guardados.")
                win.destroy()
            except ValueError:
                messagebox.showerror("Error", "Los valores deben ser numéricos.")

        btn_save = ctk.CTkButton(win, text="GUARDAR CAMBIOS", command=save, fg_color="#2ECC71")
        btn_save.pack(side="bottom", pady=20)

    def generate_help_pdf(self):
        """Genera el manual de configuración y abre la carpeta contenedora."""
        dest = os.path.join(os.getcwd(), "Manual_Configuracion_OCR.pdf")
        if generar_pdf_instrucciones(dest):
            self.show_toast("PDF generado con éxito. 📄", "#2ECC71")
            # Abrir el archivo automáticamente según el SO
            try:
                if platform.system() == "Windows":
                    os.startfile(dest)
                elif platform.system() == "Darwin": # macOS
                    import subprocess
                    subprocess.call(["open", dest])
                else: # Linux
                    import subprocess
                    subprocess.call(["xdg-open", dest])
            except Exception:
                messagebox.showinfo("Ayuda", f"Manual generado en:\n{dest}")
        else:
            messagebox.showerror("Error", "No se pudo generar el manual PDF.")


    def update_ocr_health(self):
        """Verifica el estado de los motores OCR y actualiza los indicadores en la Sidebar."""
        msg = ""
        estado = verificar_dependencias_ocr()
        quota_status = self.db.get_vision_quota_status()
        quota_text = ""
        if quota_status.get("remaining") is None:
            quota_text = f" (Usadas: {quota_status.get('usage') or 0})"
        else:
            quota_text = f" (Restantes: {quota_status.get('remaining')}/{quota_status.get('quota')})"
        
        # Actualizar Tesseract
        if estado["tesseract"]:
            self.tess_label.configure(text=f"✅ Tesseract OK", text_color="#A6E3A1")
        else:
            self.tess_label.configure(text=f"❌ Tesseract (Falta)", text_color="#F38BA8")
            msg += "• Tesseract OCR no está instalado o configurado.\n"

        # Actualizar Vision
        self.vision_label.configure(text="🚫 Cloud Vision deshabilitado", text_color="#7F8C8D")
        
        # Si falta Tesseract y es la primera vez, avisar
        if not estado["tesseract"] and not hasattr(self, '_notified_ocr_error'):
            self._notified_ocr_error = True
            if messagebox.askyesno("Configuración OCR", 
                                   "No hemos detectado Tesseract OCR instalado.\n\n"
                                   "¿Deseas abrir el Manual de Configuración en PDF para solucionarlo?"):
                self.generate_help_pdf()

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
            # Usar fuente compatible con Linux
            system = platform.system()
            list_font = "DejaVu Sans" if system == "Linux" else ("Segoe UI" if system == "Windows" else "Helvetica")
            listbox = tk.Listbox(parent, bg="#1a1a1a", fg="white", borderwidth=0, font=(list_font, 10))
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
        # Mover grab_set() dentro del callback para evitar errores en Linux
        dialog.after(100, lambda: [dialog.lift(), dialog.focus_force(), dialog.grab_set()])

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

            try:
                # 1. Detector de corrección: Si el código cambió, el ANTERIOR era basura/ruido
                original_code = cupon_data['cupon']
                if original_code and new_codigo != original_code:
                    # Aprender que el código detectado automáticamente era INCORRECTO
                    self.learning_system.learn_from_feedback(original_code, cupon_data['tienda'], False)
                    # Añadir a la base de datos de términos basura
                    self.db.add_false_positive_term(original_code)
                    if self.false_positive_cache is not None:
                        self.false_positive_cache.add(original_code.upper())

                # 2. Actualizar datos en la base de datos
                self.db.update_notification(
                    self.selected_cupon_id, 
                    cupon=new_codigo, 
                    tienda=new_tienda, 
                    descuento=new_desc,
                    usuario_valido=1, 
                    es_valido=1
                )

                # 2.1 Guardar como cupón positivo para futuras apariciones
                self.db.add_positive_coupon(new_codigo, new_tienda)
                
                # 3. ALIMENTAR EL SISTEMA DE APRENDIZAJE (Supervisado)
                self.learning_system.learn_from_feedback(
                    new_codigo, 
                    new_tienda, 
                    es_valido=True, 
                    contexto=cupon_data.get('contexto')
                )
                
                self.show_toast(f"IA Entrenada: {new_tienda} reconoce {new_codigo}", "#2ECC71")
                dialog.destroy()
                self.load_notifications()
                
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar: {e}")

        ctk.CTkButton(dialog, text="✅ CONFIRMAR Y ENTRENAR IA", command=save_and_train, 
                       fg_color="#27AE60", hover_color="#219150").pack(pady=20)
        
        ctk.CTkButton(dialog, text="Cancelar", command=dialog.destroy, fg_color="transparent").pack()

    def copy_and_open_selected(self):
        tree = self.current_tree or self.trees["pending"]
        sel = tree.selection()
        if sel:
            vals = tree.item(sel[0], "values")
            # N°, IA, Cupón, Tienda... indices han cambiado por la nueva columna
            self.root.clipboard_clear()
            self.root.clipboard_append(vals[2])
            if vals[6]: webbrowser.open(vals[6])

    def copy_selection(self):
        tree = self.current_tree or self.trees["pending"]
        sel = tree.selection()
        if sel:
            # Índice 2 es el Cupón ahora
            codes = [tree.item(i, "values")[2] for i in sel]
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
            # N°, IA, Cupón, Tienda, Asunto, Descuento, URL, Confianza, Fecha, Expira
            details = (f"Tipo IA: {vals[1]}\n"
                       f"Código: {vals[2]}\n"
                       f"Tienda: {vals[3]}\n"
                       f"Asunto: {vals[4]}\n"
                       f"Descuento: {vals[5]}\n"
                       f"Confianza: {vals[7]}\n"
                       f"Fecha Detección: {vals[8]}\n"
                       f"Fecha Expiración: {vals[9]}")
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
        total_records = self.db.get_notifications_count()
        items = [
            ("Cupones Validados", str(stats['valid_feedback']), "#2ECC71"),
            ("Tiendas Detectadas", str(stats['stores_learned']), "#3498DB"),
            ("Ahorros Estimados", f"${stats['valid_feedback'] * 5}", "#F1C40F"), # Estimación ficticia de $5 por cupón
            ("Nivel de Aprendizaje", f"{total_records} registros", "#cba6f7")
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
        total_xp = self.db.get_experience_total()
        
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
        
        # Barra de progreso de entrenamiento (experiencia)
        prog_val = min(total_xp / 500, 1.0) # 500 pts para el máximo visual
        prog = ctk.CTkProgressBar(ia_frame, progress_color=ia_color)
        prog.pack(fill="x", padx=50, pady=(0, 15))
        prog.set(prog_val)

        # 1.2 Historial de Experiencia
        ctk.CTkLabel(container, text="Historial de Experiencia", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(10, 5))
        xp_frame = ctk.CTkFrame(container)
        xp_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(xp_frame, text=f"XP Total: {total_xp}", font=ctk.CTkFont(size=14, weight="bold"), text_color="#F1C40F").pack(anchor="w", padx=15, pady=(10, 5))

        xp_list = ctk.CTkScrollableFrame(xp_frame, height=140)
        xp_list.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        history = self.db.get_experience_history(limit=30)
        if history:
            for points, reason, created_at in history:
                row = ctk.CTkFrame(xp_list, fg_color="transparent")
                row.pack(fill="x", pady=2)
                color = "#2ECC71" if points >= 0 else "#E74C3C"
                ctk.CTkLabel(row, text=f"{points:+d}", width=50, text_color=color, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=str(reason)[:80], anchor="w").pack(side="left", padx=5)
                ctk.CTkLabel(row, text=str(created_at), text_color="gray", anchor="e").pack(side="right")
        else:
            ctk.CTkLabel(xp_list, text="Sin historial aún", text_color="gray").pack(pady=10)

        # 1.3 Cupones agrupados por score
        ctk.CTkLabel(container, text="Cupones agrupados por score", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(10, 5))
        grp_frame = ctk.CTkFrame(container)
        grp_frame.pack(fill="both", padx=20, pady=10)

        header = ctk.CTkFrame(grp_frame, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkLabel(header, text="Cupón", width=160, anchor="w", text_color="gray").pack(side="left")
        ctk.CTkLabel(header, text="Tienda", width=190, anchor="w", text_color="gray").pack(side="left")
        ctk.CTkLabel(header, text="Estado", width=90, anchor="w", text_color="gray").pack(side="left")
        ctk.CTkLabel(header, text="Score", width=70, anchor="w", text_color="gray").pack(side="left")
        ctk.CTkLabel(header, text="Δ", width=60, anchor="w", text_color="gray").pack(side="left")
        ctk.CTkLabel(header, text="Avg", width=70, anchor="w", text_color="gray").pack(side="left")
        ctk.CTkLabel(header, text="Total", width=50, anchor="w", text_color="gray").pack(side="left")

        grp_list = ctk.CTkScrollableFrame(grp_frame, height=220)
        grp_list.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        grouped = self.db.get_coupon_score_stats(limit=50)
        if grouped:
            for i, (cupon, tienda, avg_score, total, last_score, prev_score, last_estado, last_usr_val, last_es_val) in enumerate(grouped):
                row_color = "#1f2330" if i % 2 == 0 else "#1b1f2a"
                row = ctk.CTkFrame(grp_list, fg_color=row_color)
                row.pack(fill="x", pady=2)
                # Estado del último registro
                if last_estado == 'expirado':
                    estado = "Expirado"
                    estado_color = "#7f8c8d"
                elif last_usr_val == 1 and last_es_val == 1:
                    estado = "Validado"
                    estado_color = "#2ECC71"
                elif last_usr_val == 1 and last_es_val == 0:
                    estado = "Descartado"
                    estado_color = "#E74C3C"
                else:
                    estado = "Pendiente"
                    estado_color = "#F1C40F"

                # Score actual y delta
                score_text = f"{float(last_score):.2f}" if last_score is not None else "---"
                if prev_score is None or last_score is None:
                    delta_text = "--"
                    delta_color = "gray"
                else:
                    delta = float(last_score) - float(prev_score)
                    delta_text = f"{delta:+.2f}"
                    delta_color = "#2ECC71" if delta >= 0 else "#E74C3C"

                ctk.CTkLabel(row, text=str(cupon), width=160, anchor="w").pack(side="left", padx=6)
                ctk.CTkLabel(row, text=str(tienda), width=190, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=estado, width=90, anchor="w", text_color=estado_color).pack(side="left")
                ctk.CTkLabel(row, text=score_text, width=70, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=delta_text, width=60, anchor="w", text_color=delta_color).pack(side="left")
                ctk.CTkLabel(row, text=f"{avg_score:.2f}", width=70, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=str(total), width=50, anchor="w").pack(side="left")
        else:
            ctk.CTkLabel(grp_list, text="Sin datos todavía", text_color="gray").pack(pady=10)

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
            
            # Actualizar caché en tiempo real para reflejar en UI inmediatamente
            if code:
                self.false_positive_cache.add(code.upper())
            
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
