# Instalación en Linux - CodeNotificator

Este documento proporciona instrucciones detalladas para instalar y ejecutar CodeNotificator en sistemas Linux.

## Dependencias del Sistema

### Debian/Ubuntu/Mint

```bash
sudo apt install python3-tk libnotify-bin python3-dbus tesseract-ocr
```

### Arch Linux

```bash
sudo pacman -S tk libnotify python-dbus tesseract
```

### Fedora/RHEL/CentOS

```bash
sudo dnf install python3-tkinter libnotify dbus-python tesseract
```

## Dependencias de Python

1. **Crear entorno virtual**:

   ```bash
   python3 -m venv venv
   ```

2. **Activar el entorno virtual**:

   ```bash
   source venv/bin/activate
   ```

3. **Instalar dependencias de Python**:
   ```bash
   pip install -r requirements.txt
   ```

## Ejecutar la Aplicación

### Opción 1: Script automático

```bash
chmod +x run_linux.sh
./run_linux.sh
```

### Opción 2: Manual

```bash
source venv/bin/activate
python3 main.py
```

## Notas Importantes

### Notificaciones del Sistema

Para que las notificaciones del sistema funcionen correctamente en Linux, necesitas tener instalado `python3-dbus`. Este paquete **NO** se puede instalar vía pip, debe instalarse desde el gestor de paquetes de tu distribución.

**Sin python3-dbus**: Verás una advertencia pero la aplicación funcionará (solo que sin notificaciones del sistema).

### Tesseract OCR

CodeNotificator usa Tesseract para OCR (reconocimiento óptico de caracteres). Asegúrate de tenerlo instalado:

```bash
# Verificar instalación
tesseract --version
```

### Fuentes

La aplicación detecta automáticamente el sistema operativo y usa:

- **Linux**: DejaVu Sans (fuente estándar)
- **Windows**: Segoe UI
- **macOS**: SF Pro Display

## Solución de Problemas

### Error: "libtk8.6.so: cannot open shared object file"

**Solución**: Instala python3-tk

```bash
# Debian/Ubuntu
sudo apt install python3-tk

# Arch
sudo pacman -S tk
```

### Advertencia: "The Python dbus package is not installed"

**Solución**: Instala python-dbus desde tu gestor de paquetes

```bash
# Debian/Ubuntu
sudo apt install python3-dbus

# Arch
sudo pacman -S python-dbus
```

### La ventana de configuración no muestra texto

Este problema ha sido corregido en la última versión. Asegúrate de tener la versión más reciente del código.

## Compatibilidad

Probado en:

- ✅ Ubuntu 20.04+
- ✅ Arch Linux
- ✅ Debian 11+
- ✅ Linux Mint 20+

## Soporte

Si encuentras problemas específicos de Linux, por favor reporta:

1. Distribución y versión
2. Versión de Python (`python3 --version`)
3. Mensaje de error completo
