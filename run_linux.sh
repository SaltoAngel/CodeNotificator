#!/bin/bash

echo "--- Iniciando CodeNotificator para Linux ---"

# 1. Verificar si existe entorno virtual
if [ ! -d "venv" ]; then
    echo "Creando entorno virtual..."
    python3 -m venv venv
fi

# 2. Activar entorno virtual
source venv/bin/activate

# 3. Instalar dependencias necesarias del sistema si es posible
# Nota: Esto suele requerir sudo, mejor informar al usuario
echo "Asegúrate de tener instalado: sudo apt install python3-tk libnotify-bin"

# 4. Instalar librerías de Python
echo "Verificando dependencias..."
pip install -r requirements.txt

# 5. Lanzar aplicación
echo "Lanzando aplicación..."
python3 main.py

# 6. Desactivar al terminar
deactivate
