from datetime import datetime, timedelta
import logging

logger = logging.getLogger('CouponNotifier')

try:
    from plyer import notification
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False
    logger.warning("Plyer no instalado. Las notificaciones estarán desactivadas.")
    logger.warning("Instala con: pip install plyer")


class SystemNotifier:
    def __init__(self, enabled=True):
        self.enabled = enabled and NOTIFICATIONS_AVAILABLE
        self.last_notification = None
        self.min_interval = timedelta(seconds=30)

    def show_notification(self, title, message, duration=5):
        if not self.enabled:
            return

        now = datetime.now()
        if self.last_notification and (now - self.last_notification) < self.min_interval:
            return

        try:
            notification.notify(
                title=title,
                message=message,
                app_name="CodeNotificator",
                timeout=duration
            )

            self.last_notification = now
            logger.info(f"Notificación: {title}")

        except Exception as e:
            logger.error(f"Error en notificación: {e}")
            logger.warning(f"🔔 {title}: {message}")

    def notify_new_coupons(self, count, coupons_list):
        if count == 0 or not self.enabled:
            return

        title = "🎉 Nuevos Cupones"

        if count == 1:
            cupon = coupons_list[0]
            message = f"{cupon['codigo']} - {cupon['tienda']}"
            if cupon.get('descuento'):
                message += f" ({cupon['descuento']})"
        elif count <= 3:
            cupones_str = ", ".join([c['codigo'] for c in coupons_list[:3]])
            message = f"{count} nuevos: {cupones_str}"
        else:
            message = f"¡{count} cupones nuevos!"

        self.show_notification(title, message)

    def notify_scan_complete(self, total_found, new_count):
        if not self.enabled:
            return

        title = "🔍 Escaneo Completado"
        message = f"{new_count} nuevos ({total_found} total)" if new_count > 0 else f"{total_found} en total"

        self.show_notification(title, message)

    def enable(self):
        self.enabled = True and NOTIFICATIONS_AVAILABLE

    def disable(self):
        self.enabled = False
