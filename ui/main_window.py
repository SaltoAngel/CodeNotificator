import os
import queue
import threading
import logging
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
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


class CouponNotifierApp:
    def __init__(self, root, db, learning_system, processor, notifier):
        self.root = root
        self.root.title("Notificador de Cupones Inteligente")
        self.root.geometry("1000x700")

        try:
            self.root.iconbitmap('icon.ico')
        except Exception:
            pass

        self.db = db
        self.learning_system = learning_system
        self.processor = processor
        self.notifier = notifier

        self.queue = queue.Queue()
        self.scanning = False
        self.auto_scan_id = None
        self.selected_cupon_id = None
        self.tray_icon = None
        self.tray_thread = None

        self.setup_ui()
        self.load_notifications()
        self.check_queue()
        self.check_configuration()

        self.setup_tray()

        logger.info("Aplicación iniciada correctamente")

    def setup_ui(self):
        COLORS = {
            'primary': '#2C3E50',
            'secondary': '#3498DB',
            'accent': '#E74C3C',
            'success': '#27AE60',
            'warning': '#F39C12',
            'light': '#ECF0F1',
            'dark': '#2C3E50',
        }

        main_frame = tk.Frame(self.root, bg=COLORS['light'])
        main_frame.pack(fill="both", expand=True)

        top_frame = tk.Frame(main_frame, bg=COLORS['primary'], height=70)
        top_frame.pack(fill="x", pady=(0, 5))

        tk.Label(top_frame, text="🤖 NOTIFICADOR DE CUPONES INTELIGENTE",
                 font=("Arial", 16, "bold"), bg=COLORS['primary'], fg="white").pack(pady=15)

        toolbar = tk.Frame(main_frame, bg=COLORS['light'])
        toolbar.pack(fill="x", padx=10, pady=5)

        buttons = [
            ("⚙️ Configuración", self.open_config, COLORS['secondary']),
            ("🔍 Escanear Ahora", self.start_scan, COLORS['success']),
            ("📊 Estadísticas", self.show_stats, COLORS['warning']),
            ("📧 Ver Correo", self.open_selected_email, COLORS['secondary']),
            ("🧾 Aplicar cupón", self.copy_and_open_selected, COLORS['secondary']),
            ("📚 Diccionarios", self.open_dictionary_manager, COLORS['secondary']),
            ("📋 Copiar", self.copy_selection, COLORS['secondary']),
            ("🧹 Limpiar 30 días", self.clear_old_coupons, COLORS['accent']),
            ("🗑️ Limpiar todo", self.clear_all, COLORS['accent']),
        ]

        for text, command, color in buttons:
            btn = tk.Button(toolbar, text=text, command=command,
                            bg=color, fg="white", font=("Arial", 10),
                            relief="flat", padx=15, pady=5)
            btn.pack(side="left", padx=2)

        filter_frame = tk.Frame(main_frame, bg=COLORS['light'])
        filter_frame.pack(fill="x", padx=10, pady=(0, 5))

        tk.Label(filter_frame, text="Filtro:", bg=COLORS['light']).pack(side="left", padx=(0, 5))
        self.filter_var = tk.StringVar(value="Todos")
        self.store_filter_var = tk.StringVar()

        self.filter_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=["Todos", "Recientes (7 días)", "Alta confianza (>=80%)", "Por tienda"],
            state="readonly",
            width=24
        )
        self.filter_combo.pack(side="left", padx=5)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        self.store_filter_entry = tk.Entry(filter_frame, textvariable=self.store_filter_var, width=20)
        self.store_filter_entry.pack(side="left", padx=5)

        tk.Button(filter_frame, text="Aplicar", command=self.apply_filters).pack(side="left", padx=5)

        self.progress_frame = tk.Frame(main_frame, bg=COLORS['light'])
        self.progress_frame.pack(fill="x", padx=10, pady=5)

        self.progress_bar = ttk.Progressbar(self.progress_frame, mode='indeterminate')
        self.progress_bar.pack(fill="x")
        self.progress_frame.pack_forget()

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

        list_frame = tk.Frame(main_frame, bg=COLORS['light'])
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.current_tree = None
        self.trees = {}

        notebook = ttk.Notebook(list_frame)
        notebook.pack(fill="both", expand=True)

        def create_tree(parent):
            columns = ("N°", "Cupón", "Tienda", "Asunto", "Descuento", "URL", "Confianza", "Fecha")
            tree = ttk.Treeview(parent, columns=columns, show="headings", height=15)

            col_widths = [50, 140, 120, 240, 80, 200, 80, 140]
            for col, width in zip(columns, col_widths):
                tree.heading(col, text=col)
                tree.column(col, width=width, anchor="center")

            vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
            hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

            tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")

            parent.grid_columnconfigure(0, weight=1)
            parent.grid_rowconfigure(0, weight=1)

            tree.tag_configure("conf_high", background="#E8F5E9")
            tree.tag_configure("conf_mid", background="#FFFDE7")
            tree.tag_configure("conf_low", background="#FFEBEE")
            tree.tag_configure("flag_false", background="#FFCDD2")
            tree.tag_configure("flag_positive", background="#C8E6C9")

            tree.bind('<<TreeviewSelect>>', self.on_tree_select)
            tree.bind("<Double-Button-1>", self.show_details)
            tree.bind("<Button-3>", self.on_tree_right_click)

            return tree

        pending_frame = tk.Frame(notebook)
        validated_frame = tk.Frame(notebook)
        false_frame = tk.Frame(notebook)

        notebook.add(pending_frame, text="Cupones Pendientes")
        notebook.add(validated_frame, text="Validados")
        notebook.add(false_frame, text="Falsos Positivos")

        self.trees["pending"] = create_tree(pending_frame)
        self.trees["validated"] = create_tree(validated_frame)
        self.trees["false"] = create_tree(false_frame)

        bottom_frame = tk.Frame(main_frame, bg=COLORS['dark'])
        bottom_frame.pack(fill="x", side="bottom", pady=(5, 0))

        self.count_label = tk.Label(bottom_frame, text="0 cupones",
                                    font=("Arial", 10, "bold"), bg=COLORS['dark'], fg="white")
        self.count_label.pack(side="left", padx=20, pady=8)

        self.stats_label = tk.Label(bottom_frame, text="ML: 0 ejemplos",
                                    font=("Arial", 9), bg=COLORS['dark'], fg="#BDC3C7")
        self.stats_label.pack(side="left", padx=20, pady=8)

        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Ir a la Web", command=self.open_selected_url)
        self.context_menu.add_command(label="Copiar Código", command=self.copy_selected_code)
        self.context_menu.add_command(label="Copiar y abrir", command=self.copy_and_open_selected)
        self.context_menu.add_command(label="Ver Correo Original", command=self.open_selected_email)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Marcar como falso positivo", command=self.add_false_positive_from_selection)
        self.context_menu.add_command(label="Marcar como cupón confirmado", command=self.add_positive_coupon_from_selection)
        self.context_menu.add_command(label="Eliminar", command=self.delete_selected_coupon)

    def setup_tray(self):
        if not TRAY_AVAILABLE:
            return

        if Image is None:
            return

        try:
            icon_image = self._create_tray_icon()
            menu = pystray.Menu(
                pystray.MenuItem("Mostrar ventana", self._tray_show_window),
                pystray.MenuItem("Escanear ahora", self._tray_scan_now),
                pystray.MenuItem("Salir", self._tray_quit_app)
            )

            self.tray_icon = pystray.Icon("CodeNotificator", icon_image, "CodeNotificator", menu)
            self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            self.tray_thread.start()
        except Exception as e:
            logger.error(f"Error inicializando bandeja del sistema: {e}")

    def _create_tray_icon(self):
        try:
            icon_path = os.path.join(os.path.dirname(__file__), '..', 'icon.ico')
            icon_path = os.path.normpath(icon_path)
            if os.path.exists(icon_path):
                return Image.open(icon_path)
        except Exception:
            pass

        img = Image.new('RGB', (64, 64), color=(52, 152, 219))
        return img

    def _tray_show_window(self, icon=None, item=None):
        self.root.after(0, self.show_window)

    def _tray_scan_now(self, icon=None, item=None):
        self.root.after(0, self.start_scan)

    def _tray_quit_app(self, icon=None, item=None):
        self.root.after(0, self.quit_app)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        self.root.withdraw()

    def on_tree_select(self, event):
        tree = event.widget
        self.current_tree = tree
        selection = tree.selection()
        if selection:
            self.selected_cupon_id = int(selection[0])
            self.valid_btn.config(state="normal")
            self.invalid_btn.config(state="normal")
        else:
            self.selected_cupon_id = None
            self.valid_btn.config(state="disabled")
            self.invalid_btn.config(state="disabled")

    def on_tree_right_click(self, event):
        tree = event.widget
        self.current_tree = tree
        item = tree.identify_row(event.y)
        if item:
            tree.selection_set(item)
            tree.focus(item)
            try:
                self.selected_cupon_id = int(item)
            except ValueError:
                self.selected_cupon_id = None
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def mark_as_valid(self):
        if not self.selected_cupon_id:
            return

        cursor = self.db.conn.cursor()
        cursor.execute('SELECT cupon, tienda, contexto FROM notifications WHERE id = ?', (self.selected_cupon_id,))
        cupon_data = cursor.fetchone()

        if cupon_data:
            cupon_text, tienda, contexto = cupon_data

            self.learning_system.learn_from_feedback(cupon_text, tienda, True, contexto=contexto)
            self.db.add_positive_coupon(cupon_text, tienda)

            self.db.update_cupon_validity(self.selected_cupon_id, True, 1.0)
            self.db.add_user_feedback(self.selected_cupon_id, cupon_text, tienda, True, "Marcado como válido")

            self.load_notifications()
            self.update_stats_display()

            self.notifier.show_notification("✅ Feedback Registrado",
                                            f"'{cupon_text}' marcado como válido")

            messagebox.showinfo("Éxito", "¡Gracias por tu feedback! El sistema ha aprendido de este ejemplo.")

    def mark_as_invalid(self):
        if not self.selected_cupon_id:
            return

        cursor = self.db.conn.cursor()
        cursor.execute('SELECT cupon, tienda, contexto FROM notifications WHERE id = ?', (self.selected_cupon_id,))
        cupon_data = cursor.fetchone()

        if cupon_data:
            cupon_text, tienda, contexto = cupon_data

            self.learning_system.learn_from_feedback(cupon_text, tienda, False, contexto=contexto)

            self.db.update_cupon_validity(self.selected_cupon_id, False, 0.0)
            self.db.add_user_feedback(self.selected_cupon_id, cupon_text, tienda, False, "Marcado como inválido")

            self.load_notifications()
            self.update_stats_display()

            self.notifier.show_notification("❌ Feedback Registrado",
                                            f"'{cupon_text}' marcado como inválido")

            messagebox.showinfo("Éxito", "Feedback registrado. El sistema mejorará sus detecciones.")

    def update_stats_display(self):
        stats = self.learning_system.get_stats()
        self.stats_label.config(text=f"ML: {stats['total_feedback']} ejemplos")

    def apply_filters(self):
        if self.filter_var.get() != "Por tienda":
            self.store_filter_var.set("")
        self.load_notifications()

    def load_notifications(self):
        for tree in self.trees.values():
            for item in tree.get_children():
                tree.delete(item)

        min_confidence = None
        days = None
        tienda = None

        selected_filter = self.filter_var.get() if hasattr(self, "filter_var") else "Todos"
        if selected_filter == "Recientes (7 días)":
            days = 7
        elif selected_filter == "Alta confianza (>=80%)":
            min_confidence = 0.8
        elif selected_filter == "Por tienda":
            tienda = self.store_filter_var.get().strip() if hasattr(self, "store_filter_var") else None

        pending = self.db.get_notifications(limit=50, min_confidence=min_confidence, days=days, tienda=tienda, usuario_valido=False)
        validated = self.db.get_notifications(limit=50, min_confidence=min_confidence, days=days, tienda=tienda, usuario_valido=True, es_valido=True)
        false_pos = self.db.get_notifications(limit=50, min_confidence=min_confidence, days=days, tienda=tienda, usuario_valido=True, es_valido=False)

        def fill_tree(tree, rows):
            for idx, notif in enumerate(rows, 1):
                cupon_id, cupon, tienda, asunto, url, descuento, estado, usuario_valido, es_valido, confianza, fecha = notif

                if isinstance(fecha, str):
                    try:
                        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d %H:%M:%S")
                        fecha_str = fecha_dt.strftime("%d/%m/%Y %I:%M %p")
                    except Exception:
                        fecha_str = fecha
                else:
                    fecha_str = str(fecha)

                conf_str = f"{confianza:.0%}"

                if self.db.is_false_positive_term(cupon) or (usuario_valido and not es_valido):
                    tag = "flag_false"
                elif self.db.is_positive_coupon(cupon) or (usuario_valido and es_valido):
                    tag = "flag_positive"
                elif confianza >= 0.8:
                    tag = "conf_high"
                elif confianza >= 0.5:
                    tag = "conf_mid"
                else:
                    tag = "conf_low"

                tree.insert("", "end", iid=str(cupon_id), values=(
                    idx, cupon, tienda, asunto or "", descuento or "", url, conf_str, fecha_str
                ), tags=(tag,))

        fill_tree(self.trees["pending"], pending)
        fill_tree(self.trees["validated"], validated)
        fill_tree(self.trees["false"], false_pos)

        total_count = len(pending) + len(validated) + len(false_pos)
        self.count_label.config(text=f"{total_count} cupones encontrados")
        self.update_stats_display()

    def show_stats(self):
        stats = self.learning_system.get_stats()
        patterns = self.db.get_top_patterns(5)

        stats_window = tk.Toplevel(self.root)
        stats_window.title("📊 Estadísticas del Sistema")
        stats_window.geometry("500x400")

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
            coupons, message, error_type = self.processor.scan_emails()
            if error_type:
                self.queue.put(("scan_error", error_type, message))
            else:
                self.queue.put(("scan_complete", coupons, message))
        except Exception as e:
            logger.error(f"Error en escaneo: {e}")
            self.queue.put(("scan_error", "unknown", str(e)))

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

                elif msg_type == "scan_error":
                    error_type, error_msg = args
                    self.scanning = False
                    self.progress_bar.stop()
                    self.progress_frame.pack_forget()
                    self.show_scan_error_dialog(error_type, error_msg)

        except queue.Empty:
            pass

        self.root.after(100, self.check_queue)

    def show_scan_error_dialog(self, error_type, error_msg):
        dialog = tk.Toplevel(self.root)
        dialog.title("Problema de conexión")
        dialog.geometry("420x220")
        dialog.grab_set()

        if error_type == "auth":
            title = "No se pudo autenticar con Gmail"
            body = (
                "Parece que las credenciales no están configuradas o expiraron.\n\n"
                "Puedes reconectar tu cuenta para continuar."
            )
        elif error_type == "network":
            title = "Problema de conexión"
            body = (
                "No se pudo completar el escaneo.\n\n"
                "Verifica tu conexión a internet e intenta de nuevo."
            )
        else:
            title = "Error inesperado"
            body = "Ocurrió un error durante el escaneo."

        tk.Label(dialog, text=title, font=("Arial", 12, "bold")).pack(pady=10)
        tk.Label(dialog, text=body, wraplength=380, justify="left").pack(pady=5)

        details = tk.Label(dialog, text=f"Detalles: {error_msg}", fg="#7f8c8d", wraplength=380, justify="left")
        details.pack(pady=5)

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=10)

        if error_type == "auth":
            tk.Button(btn_frame, text="Reconectar", command=lambda: [dialog.destroy(), self.open_config()]).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Cerrar", command=dialog.destroy).pack(side="left", padx=5)

    def copy_selection(self):
        tree = self.current_tree or self.trees.get("pending")
        selection = tree.selection()
        if not selection:
            return

        texts = []
        for item in selection:
            values = tree.item(item, "values")
            if values:
                texts.append(values[1])

        if texts:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(texts))
            messagebox.showinfo("Copiado", f"{len(texts)} cupones copiados")

    def show_details(self, event):
        tree = event.widget
        self.current_tree = tree
        selection = tree.selection()
        if not selection:
            return

        item = tree.item(selection[0])
        values = item['values']

        details = f"""
        🎫 Código: {values[1]}
        🏪 Tienda: {values[2]}
        📨 Asunto: {values[3] if values[3] else "No disponible"}
        💰 Descuento: {values[4] if values[4] else "No especificado"}
        🔗 URL: {values[5] if values[5] else "No disponible"}
        📊 Confianza: {values[6]}
        📅 Fecha: {values[7]}
        """

        messagebox.showinfo("Detalles del Cupón", details)

    def clear_all(self):
        if messagebox.askyesno("Confirmar", "¿Eliminar todos los cupones?"):
            cursor = self.db.conn.cursor()
            cursor.execute("DELETE FROM notifications")
            self.db.conn.commit()
            self.load_notifications()
            messagebox.showinfo("Éxito", "Todos los cupones han sido eliminados")

    def clear_old_coupons(self):
        if not messagebox.askyesno("Confirmar", "¿Eliminar cupones con más de 30 días?"):
            return
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.db.conn.cursor()
        cursor.execute("DELETE FROM notifications WHERE Fecha < ?", (cutoff,))
        deleted = cursor.rowcount
        self.db.conn.commit()
        self.load_notifications()
        messagebox.showinfo("Éxito", f"Se eliminaron {deleted} cupones antiguos")

    def open_config(self):
        config_window = tk.Toplevel(self.root)
        config_window.title("Configuración")
        config_window.geometry("500x400")

        notebook = ttk.Notebook(config_window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        general_frame = ttk.Frame(notebook)
        notebook.add(general_frame, text="General")

        config = self.db.get_config()

        tk.Label(general_frame, text="Intervalo (minutos):").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        intervalo_var = tk.StringVar(value=str(config[1] if config else 30))
        tk.Entry(general_frame, textvariable=intervalo_var, width=10).grid(row=0, column=1, padx=10, pady=10)

        gmail_frame = ttk.Frame(notebook)
        notebook.add(gmail_frame, text="Gmail")

        tk.Label(gmail_frame, text="Configura Gmail API:", font=("Arial", 11, "bold")).pack(pady=10)

        def open_help():
            webbrowser.open("https://developers.google.com/gmail/api/quickstart/python#authorize_credentials_for_a_desktop_application")

        header_frame = tk.Frame(gmail_frame)
        header_frame.pack(fill="x", padx=10)
        tk.Label(header_frame, text="Conectar con Gmail", font=("Arial", 12, "bold")).pack(side="left")
        tk.Button(header_frame, text="❓ Cómo obtener credentials.json", command=open_help,
                  fg="blue", cursor="hand2", relief="flat").pack(side="right")

        body = tk.Frame(gmail_frame, padx=10, pady=10)
        body.pack(fill="both", expand=True)

        instructions = (
            "1. Abre Google Cloud Console y crea un proyecto 'CodeNotificator'.\n"
            "2. Habilita la Gmail API.\n"
            "3. Configura la pantalla de consentimiento (External) y añade tu correo como Test User.\n"
            "4. Crea credenciales: 'OAuth Client ID' -> Aplicación de Escritorio.\n"
            "5. Descarga el archivo 'credentials.json'.\n"
        )
        tk.Label(body, text=instructions, justify="left", wraplength=480).pack(pady=8)

        config = self.db.get_config()
        current_email = config[9] if config and len(config) > 9 else None
        status_text = "🔴 No conectado"
        if current_email:
            status_text = f"🟢 Conectado como: {current_email}"

        status_label = tk.Label(body, text=f"Estado: {status_text}", font=("Arial", 10, "italic"))
        status_label.pack(pady=6)

        def setup_gmail():
            try:
                messagebox.showinfo("Proceso de Autorización", (
                    "Se abrirá el navegador para autorizar el acceso a tu cuenta de Gmail.\n\n"
                    "Si Google muestra 'La app no está verificada', haz clic en 'Configuración avanzada'\n"
                    "y luego en 'Ir a CodeNotificator (no seguro)'."))

                from core.gmail_engine import GmailAuthenticator
                tokens = GmailAuthenticator.obtener_tokens_interactivo()

                if tokens:
                    self.db.update_config(
                        client_id=tokens.get('client_id'),
                        client_secret=tokens.get('client_secret'),
                        refresh_token=tokens.get('refresh_token'),
                        user_email=tokens.get('user_email')
                    )

                    if tokens.get('user_email'):
                        status_label.config(text=f"Estado: 🟢 Conectado como: {tokens.get('user_email')}", fg="green")
                    else:
                        status_label.config(text="Estado: 🟡 Configurado (email no disponible)", fg="orange")

                    messagebox.showinfo("Éxito", "¡Conexión establecida correctamente!")
                    config_window.after(1200, config_window.destroy)
            except Exception as e:
                logger.error(f"Error en configuración Gmail: {e}")
                messagebox.showerror("Error de Configuración", f"No se pudo completar: {e}")

        tk.Button(body, text="🔑 Seleccionar credentials.json y Conectar",
                  command=setup_gmail, bg="#3498DB", fg="white",
                  font=("Arial", 10, "bold"), padx=20, pady=10).pack(pady=12)

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

    def open_selected_url(self):
        tree = self.current_tree or self.trees.get("pending")
        selection = tree.selection()
        if not selection:
            return
        values = tree.item(selection[0], "values")
        url = values[5] if values else ""
        cleaned = self.processor._clean_url(url)
        if cleaned:
            webbrowser.open(cleaned)
        else:
            messagebox.showwarning("URL inválida", "No hay una URL válida para este cupón")

    def copy_selected_code(self):
        tree = self.current_tree or self.trees.get("pending")
        selection = tree.selection()
        if not selection:
            return
        values = tree.item(selection[0], "values")
        if values:
            self.root.clipboard_clear()
            self.root.clipboard_append(values[1])
            messagebox.showinfo("Copiado", "Código copiado al portapapeles")

    def copy_and_open_selected(self):
        tree = self.current_tree or self.trees.get("pending")
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Por favor, selecciona un cupón de la lista.")
            return
        values = tree.item(selection[0], "values")
        if not values:
            return

        code = values[1]
        url = values[5] if len(values) > 5 else ""

        if code:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)

        cleaned = self.processor._clean_url(url)
        if cleaned:
            webbrowser.open(cleaned)
        else:
            messagebox.showwarning("URL inválida", "No hay una URL válida para este cupón")

    def open_selected_email(self):
        tree = self.current_tree or self.trees.get("pending")
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Por favor, selecciona un cupón de la lista.")
            return
        try:
            id_db = int(selection[0])
        except ValueError:
            return

        id_gmail = self.db.obtener_id_gmail_por_db(id_db)

        if id_gmail:
            url = f"https://mail.google.com/mail/u/0/#inbox/{id_gmail}"
            webbrowser.open(url)
        else:
            messagebox.showerror("Error", "No se encontró el identificador del correo original.")

    def add_false_positive_from_selection(self):
        tree = self.current_tree or self.trees.get("pending")
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Por favor, selecciona un cupón de la lista.")
            return
        values = tree.item(selection[0], "values")
        if not values:
            return
        code = values[1]
        if code:
            self.db.add_false_positive_term(code)
            messagebox.showinfo("Guardado", "Se añadió a la lista de falsos positivos.")

    def add_positive_coupon_from_selection(self):
        tree = self.current_tree or self.trees.get("pending")
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Por favor, selecciona un cupón de la lista.")
            return
        values = tree.item(selection[0], "values")
        if not values:
            return
        code = values[1]
        tienda = values[2]
        if code:
            self.db.add_positive_coupon(code, tienda)
            messagebox.showinfo("Guardado", "Se añadió a la lista de cupones confirmados.")

    def delete_selected_coupon(self):
        if not self.selected_cupon_id:
            return
        if messagebox.askyesno("Confirmar", "¿Eliminar este cupón?"):
            cursor = self.db.conn.cursor()
            cursor.execute("DELETE FROM notifications WHERE id = ?", (self.selected_cupon_id,))
            self.db.conn.commit()
            self.load_notifications()

    def open_dictionary_manager(self):
        win = tk.Toplevel(self.root)
        win.title("📚 Diccionarios de Aprendizaje")
        win.geometry("700x450")
        win.grab_set()

        container = tk.Frame(win)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        fp_frame = tk.LabelFrame(container, text="Falsos positivos", padx=10, pady=10)
        fp_frame.pack(side="left", fill="both", expand=True, padx=5)

        fp_list = tk.Listbox(fp_frame, height=15)
        fp_list.pack(fill="both", expand=True)

        fp_entry = tk.Entry(fp_frame)
        fp_entry.pack(fill="x", pady=5)

        fp_btns = tk.Frame(fp_frame)
        fp_btns.pack(fill="x")

        def refresh_fp():
            fp_list.delete(0, tk.END)
            for term in self.db.get_false_positive_terms():
                fp_list.insert(tk.END, term)

        def add_fp():
            term = fp_entry.get().strip()
            if term:
                self.db.add_false_positive_term(term)
                fp_entry.delete(0, tk.END)
                refresh_fp()

        def remove_fp():
            sel = fp_list.curselection()
            if not sel:
                return
            term = fp_list.get(sel[0])
            self.db.remove_false_positive_term(term)
            refresh_fp()

        tk.Button(fp_btns, text="Agregar", command=add_fp).pack(side="left", padx=5)
        tk.Button(fp_btns, text="Eliminar", command=remove_fp).pack(side="left", padx=5)

        pc_frame = tk.LabelFrame(container, text="Cupones confirmados", padx=10, pady=10)
        pc_frame.pack(side="left", fill="both", expand=True, padx=5)

        pc_list = tk.Listbox(pc_frame, height=15)
        pc_list.pack(fill="both", expand=True)

        pc_entry = tk.Entry(pc_frame)
        pc_entry.pack(fill="x", pady=5)

        pc_btns = tk.Frame(pc_frame)
        pc_btns.pack(fill="x")

        def refresh_pc():
            pc_list.delete(0, tk.END)
            for code, tienda in self.db.get_positive_coupons():
                label = f"{code} ({tienda})" if tienda else code
                pc_list.insert(tk.END, label)

        def add_pc():
            code = pc_entry.get().strip()
            if code:
                self.db.add_positive_coupon(code)
                pc_entry.delete(0, tk.END)
                refresh_pc()

        def remove_pc():
            sel = pc_list.curselection()
            if not sel:
                return
            label = pc_list.get(sel[0])
            code = label.split(" (")[0]
            self.db.remove_positive_coupon(code)
            refresh_pc()

        tk.Button(pc_btns, text="Agregar", command=add_pc).pack(side="left", padx=5)
        tk.Button(pc_btns, text="Eliminar", command=remove_pc).pack(side="left", padx=5)

        refresh_fp()
        refresh_pc()

    def quit_app(self):
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        self.db.close()
        self.root.destroy()

    def on_closing(self):
        if TRAY_AVAILABLE and self.tray_icon:
            self.hide_window()
            return
        self.quit_app()
