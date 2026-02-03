import os
import tkinter as tk
from tkinter import messagebox
import traceback

import customtkinter as ctk

from utils.logger import logger
from database.db_manager import DatabaseManager
from core.learning import SimpleLearningSystem
from core.gmail_engine import GmailOCRProcessor
from core.notifier import SystemNotifier
from ui.main_window import CouponNotifierApp


def main():
    try:
        # Configuración de CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        root = ctk.CTk()
        db_path = os.path.join(os.path.dirname(__file__), 'notifications.db')
        db = DatabaseManager(db_path)
        learning_system = SimpleLearningSystem(db)
        processor = GmailOCRProcessor(db, learning_system)
        notifier = SystemNotifier()
        app = CouponNotifierApp(root, db, learning_system, processor, notifier)

        root.protocol("WM_DELETE_WINDOW", app.on_closing)
        root.mainloop()

    except Exception as e:
        logger.critical(f"Error fatal: {traceback.format_exc()}")
        messagebox.showerror("Error", f"Error crítico: {str(e)}")
        raise


if __name__ == "__main__":
    main()
