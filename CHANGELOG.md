# Changelog - CodeNotificator PRO

## v2.2.0 - [Actual] 🧠 IA Potenciada & Auto-Corrección
- **Nuevo:** Sistema de "Lógica Difusa" para corregir errores de OCR automáticamente (ej: 0 -> O, 5 -> S).
- **Mejora:** El sistema ahora aprende "en negativo". Al corregir un cupón, el código anterior se marca automáticamente como Falso Positivo.
- **Mejora:** Caché en memoria RAM para listas negras, acelerando la interfaz x10.
- **Mejora:** Validación robusta en base de datos para prevenir cierres inesperados.

## v2.1.0 - Portabilidad & Distribución 📦
- **Nuevo:** Herramienta de Exportación/Importación de "Cerebro IA" (Backups de inteligencia).
- **Nuevo:** Scripts de construcción para generar .exe (Windows) y .sh (Linux).
- **Nuevo:** Pestaña "Atajos" en configuración para personalizar teclas rápidas.

## v2.0.0 - Identidad Visual & UX 🎨
- **Nuevo:** Descarga automática de logotipos de tiendas para una identificación visual rápida.
- **Nuevo:** Interfaz moderna con temas oscuros y componentes personalizados (CustomTkinter).
- **Nuevo:** Notificaciones tipo "Toast" para feedback de acciones no intrusivo.
- **Mejora:** Reescritura del motor de Gmail para mayor velocidad y menor consumo de cuota.

## v1.5.0 - Core Learning 🌱
- **Nuevo:** Primera implementación del sistema de aprendizaje de patrones.
- **Nuevo:** Detección de cupones mediante OCR (Tesseract).
