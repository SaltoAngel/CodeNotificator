import os
import subprocess
import sys

def build():
    print("--- Preparando construcción de CodeNotificator EXE ---")
    
    # Asegurar que PyInstaller esté instalado
    try:
        import PyInstaller
    except ImportError:
        print("Instalando PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Comando de PyInstaller
    # --noconsole: No abre ventana de comandos al iniciar la app
    # --onefile: Empaqueta todo en un único .exe
    # --name: Nombre del ejecutable
    # --add-data: Para incluir archivos externos si los hubiera (ej: iconos)
    # --hidden-import: Asegurar que CustomTkinter y otros se incluyan bien
    
    cmd = [
        "pyinstaller",
        "--noconsole",
        "--onefile",
        "--name", "CodeNotificator_PRO",
        "--hidden-import", "babel.numbers",
        "--hidden-import", "PIL._tkinter_finder",
        "--add-data", f"{os.path.join(os.path.dirname(sys.modules['customtkinter'].__file__))};customtkinter/",
        "main.py"
    ]
    
    print(f"Ejecutando: {' '.join(cmd)}")
    subprocess.run(cmd)
    
    print("\n--- ¡Proceso completado! ---")
    print("El ejecutable estará en la carpeta 'dist/CodeNotificator_PRO.exe'")
    print("Nota: Recuerda copiar 'notifications.db' y 'token.json' a la misma carpeta que el EXE para mantener tus datos.")

if __name__ == "__main__":
    build()
