import os
import requests
import logging
from urllib.parse import urlparse

logger = logging.getLogger('CouponNotifier')

class LogoManager:
    def __init__(self, base_dir=None):
        if base_dir is None:
            # Por defecto, en la carpeta del proyecto /assets/logos
            base_dir = os.path.join(os.getcwd(), "assets", "logos")
        
        self.base_dir = base_dir
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
            logger.info(f"Carpeta de logos creada: {self.base_dir}")
        
        self.failed_domains = set() # Evitar reintentos en la misma sesión

    def get_logo_path(self, store_name, store_url=None):
        """
        Obtiene la ruta local del logo de una tienda. 
        Si no existe, intenta descargarlo.
        """
        if not store_name or store_name.lower() == "desconocida":
            return None

        # Normalizar el nombre de la tienda para el nombre de archivo
        safe_name = "".join([c for c in store_name if c.isalnum()]).lower()
        file_path = os.path.join(self.base_dir, f"{safe_name}.png")

        # Si ya existe en caché, lo devolvemos
        if os.path.exists(file_path):
            return file_path

        # Si no existe, intentamos buscar el dominio y descargar
        domain = self._extract_domain(store_name, store_url)
        if domain and domain not in self.failed_domains:
            success = self._download_logo(domain, file_path)
            if success:
                return file_path
            else:
                self.failed_domains.add(domain) # No reintentar en esta sesión
        
        return None

    def _extract_domain(self, store_name, store_url):
        """Intenta deducir el dominio de la tienda."""
        if store_url:
            parsed = urlparse(store_url)
            if parsed.netloc:
                return parsed.netloc

        # Si no hay URL, intentamos con el nombre + .com (simple heurística)
        clean_name = store_name.lower().replace(" ", "")
        if "." in clean_name:
            return clean_name
            
        return f"{clean_name}.com"

    def _download_logo(self, domain, save_path):
        """Descarga el logo desde la API de Clearbit."""
        try:
            # Clearbit Logo API: logo.clearbit.com/DOMAIN
            url = f"https://logo.clearbit.com/{domain}?size=128&format=png"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                logger.info(f"Logo descargado para {domain}")
                return True
            else:
                # Si es 404 u otro error, marcamos como fallido sin loguear como ERROR
                logger.debug(f"Logo no disponible para {domain} (Status: {response.status_code})")
                return False
        except requests.exceptions.ConnectionError:
            # Silenciar errores de resolución DNS o falta de internet
            logger.debug(f"No se pudo resolver el dominio para el logo de {domain}")
            return False
        except Exception as e:
            logger.warning(f"Error descargando logo para {domain}: {e}")
            return False
