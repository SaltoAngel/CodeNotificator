import base64
import json
import os
import re
from datetime import datetime

import pytz
import validators

from utils.logger import logger
from core.ocr_engine import ocr_image_bytes, ocr_images_parallel
from core.extractor import CouponExtractor
from core.nlp_engine import ContextAnalyzer

DEFAULT_TIMEZONE = 'America/Caracas'


class GmailAuthenticator:
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

    @staticmethod
    def obtener_tokens_interactivo():
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()

            file_path = filedialog.askopenfilename(
                title="Selecciona tu archivo credentials.json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
            )

            root.destroy()

            if not file_path:
                raise ValueError("No se seleccionó archivo credentials.json")

            with open(file_path, 'r', encoding='utf-8') as f:
                credenciales = json.load(f)

            with open('temp_creds.json', 'w') as f:
                json.dump(credenciales, f)

            flow = InstalledAppFlow.from_client_secrets_file(
                'temp_creds.json',
                GmailAuthenticator.SCOPES
            )

            creds = flow.run_local_server(port=8080, prompt='consent')

            container = credenciales.get('installed') or credenciales.get('web')
            if not container:
                raise ValueError('El credentials.json no contiene las claves esperadas.')

            client_id = container.get('client_id')
            client_secret = container.get('client_secret')

            if not client_id or not client_secret:
                raise ValueError('Faltan client_id o client_secret en credentials.json.')

            user_email = None
            try:
                from googleapiclient.discovery import build
                service = build('gmail', 'v1', credentials=creds)
                profile = service.users().getProfile(userId='me').execute()
                user_email = profile.get('emailAddress')
            except Exception:
                user_email = None

            logger.info("Tokens obtenidos exitosamente")
            return {
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': creds.refresh_token,
                'user_email': user_email
            }

        except Exception as e:
            logger.error(f"Error obteniendo tokens: {e}")
            raise
        finally:
            if os.path.exists('temp_creds.json'):
                os.remove('temp_creds.json')

    @staticmethod
    def get_credentials(db_manager):
        from google.oauth2.credentials import Credentials
        config = db_manager.get_config()
        if not config:
            return None

        client_id = config.get('client_id')
        client_secret = config.get('client_secret')
        refresh_token = config.get('refresh_token')

        if not all([client_id, client_secret, refresh_token]):
            return None

        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=client_id,
            client_secret=client_secret,
            scopes=GmailAuthenticator.SCOPES
        )


class GmailOCRProcessor:
    def __init__(self, db_manager, learning_system=None):
        self.db = db_manager
        self.learning_system = learning_system
        self.extractor = CouponExtractor(db_manager, learning_system)
        self.nlp = ContextAnalyzer()
        self.service = None
        self.credentials = None
        self.last_auth = None
        self.auth_timeout = 3500
        self.current_email = None
        self.is_authenticated = False

        self.trusted_sender_domains = {"hollister.com"}
        self.trusted_sender_bonus = 0.15

    def _extract_sender_email(self, from_header):
        if not from_header:
            return ""
        match = re.search(r'<([^>]+)>', from_header)
        if match:
            return match.group(1).strip().lower()
        if '@' in from_header:
            return from_header.strip().lower()
        return ""

    def _is_whitelisted_sender(self, from_header):
        email = self._extract_sender_email(from_header)
        if not email:
            return False
        domain = email.split('@')[-1]
        return any(domain == d or domain.endswith("." + d) for d in self.trusted_sender_domains)

    def _format_gmail_datetime(self, internal_date_ms):
        try:
            zona_local = pytz.timezone(DEFAULT_TIMEZONE)
            fecha_utc = datetime.fromtimestamp(int(internal_date_ms) / 1000, pytz.utc)
            fecha_local = fecha_utc.astimezone(zona_local)
            return fecha_local.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            logger.error(f"Error formateando fecha Gmail: {e}")
            return None

    def authenticate(self, force_refresh=False):
        try:
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            if (not force_refresh and self.is_authenticated and self.last_auth and
                (datetime.now() - self.last_auth).seconds < self.auth_timeout):
                return True, "Autenticación en cache"

            creds = GmailAuthenticator.get_credentials(self.db)
            if not creds:
                return False, "Credenciales no configuradas"

            if creds.expired or force_refresh:
                creds.refresh(Request())

            self.service = build('gmail', 'v1', credentials=creds)
            self.credentials = creds
            self.is_authenticated = True
            self.last_auth = datetime.now()

            try:
                profile = self.service.users().getProfile(userId='me').execute()
                self.current_email = profile.get('emailAddress')
                logger.info(f"Autenticado como: {self.current_email}")
            except Exception:
                self.current_email = None

            return True, "Autenticación exitosa"

        except Exception as e:
            logger.error(f"Error de autenticación: {e}")
            self.is_authenticated = False
            return False, f"Error de autenticación: {str(e)}"

    def search_emails(self, query=None, max_results=20):
        try:
            if not self.service:
                success, _ = self.authenticate()
                if not success:
                    return []

            params = {'userId': 'me', 'maxResults': max_results}
            if query and str(query).strip():
                params['q'] = str(query).strip()

            results = self.service.users().messages().list(**params).execute()
            messages = results.get('messages', [])
            logger.info(f"Encontrados {len(messages)} correos")
            return messages

        except Exception as e:
            logger.error(f"Error buscando correos: {e}")
            return []

    def extract_texts_parallel(self, images_data):
        if not images_data:
            return []

        max_workers = min(4, os.cpu_count() or 1)
        try:
            return ocr_images_parallel(images_data, max_workers=max_workers)
        except Exception as e:
            logger.error(f"Error en OCR paralelo: {e}")
            return [ocr_image_bytes(data) for data in images_data]

    def extract_store_name_from_sender(self, from_header):
        if not from_header:
            return "Desconocida"

        try:
            match = re.match(r'^"?([^"<]+)"?\s*<[^>]+>$', from_header)
            if match:
                store_name = match.group(1).strip()
                store_name = store_name.strip('"\'')
                if store_name:
                    return store_name

            email_match = re.search(r'<([^>]+)>', from_header)
            if email_match:
                email = email_match.group(1)
                domain_match = re.search(r'@([^.]+)', email)
                if domain_match:
                    domain_part = domain_match.group(1)
                    store_name = domain_part.replace('themjewelersny', 'The M Jewelers')
                    store_name = ' '.join(word.capitalize() for word in re.split(r'[^a-zA-Z0-9]+', store_name))
                    return store_name

            if from_header and '@' in from_header:
                name_part = from_header.split('<')[0].strip()
                if name_part:
                    return name_part.strip('"\'')

            return "Desconocida"

        except Exception as e:
            logger.error(f"Error extrayendo nombre del remitente '{from_header}': {e}")
            return "Desconocida"

    def process_email(self, message_id):
        try:
            message = self.service.users().messages().get(
                userId='me', id=message_id, format='full').execute()

            headers = message.get('payload', {}).get('headers', [])

            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sin asunto')
            internal_date = message.get('internalDate')
            fecha_local = self._format_gmail_datetime(internal_date) if internal_date else None

            from_header = next((h['value'] for h in headers if h['name'] == 'From'), '')
            tienda_from_email = self.extract_store_name_from_sender(from_header)

            logger.info(f"Procesando: {subject[:50]}... (Remitente: {from_header})")
            coupons_found = []

            def extract_body(part):
                if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                    data = part['body']['data']
                    return base64.urlsafe_b64decode(data.encode('UTF-8')).decode('utf-8', errors='ignore')

                if 'parts' in part:
                    for subpart in part['parts']:
                        text = extract_body(subpart)
                        if text:
                            return text
                return ""

            def extract_images(part, images_list):
                mime_type = part.get('mimeType', '')
                body = part.get('body', {})

                if mime_type.startswith('image/'):
                    if body.get('data'):
                        images_list.append(base64.urlsafe_b64decode(body['data'].encode('UTF-8')))
                    elif body.get('attachmentId'):
                        att = self.service.users().messages().attachments().get(
                            userId='me', messageId=message_id, id=body['attachmentId']
                        ).execute()
                        if att.get('data'):
                            images_list.append(base64.urlsafe_b64decode(att['data'].encode('UTF-8')))

                if 'parts' in part:
                    for subpart in part['parts']:
                        extract_images(subpart, images_list)

            body_text = extract_body(message.get('payload', {}))
            images_data = []
            extract_images(message.get('payload', {}), images_data)

            if images_data:
                ocr_texts = self.extract_texts_parallel(images_data)
                if ocr_texts:
                    body_text = (body_text or "") + "\n" + "\n".join([t for t in ocr_texts if t])

            if body_text:
                coupon_info = self.extractor.extract_coupon_info(body_text, tienda_from_email)

                if coupon_info['codigo']:
                    if tienda_from_email and tienda_from_email != 'Desconocida':
                        coupon_info['tienda'] = tienda_from_email

                    # Análisis de NLP
                    nlp_analysis = self.nlp.analyze(subject, body_text[:2000]) # Analizar primeros 2000 chars
                    logger.info(f"NLP Score para '{subject[:30]}...': {nlp_analysis}")

                    confidence = coupon_info.get('confianza_contexto', 0.7)
                    
                    # Reforzar confianza con NLP
                    confidence = (confidence * 0.6) + (nlp_analysis['relevance_score'] * 0.4)
                    
                    if nlp_analysis['is_urgent']:
                        logger.info("🔥 Cupón urgente detectado")
                        # Podríamos agregar un marcador visual en el futuro

                    if self.learning_system:
                        learned = self.learning_system.calculate_confidence(
                            coupon_info['codigo'], coupon_info['tienda'], contexto=body_text
                        )
                        confidence = min(max((confidence * 0.4) + (learned * 0.6), 0.1), 0.99)

                    if self._is_whitelisted_sender(from_header):
                        confidence = min(confidence + self.trusted_sender_bonus, 0.99)

                    coupon_info['confianza'] = confidence
                    coupon_info['remitente'] = from_header
                    coupon_info['id_correo'] = message_id
                    coupon_info['asunto'] = subject
                    coupon_info['fecha'] = fecha_local
                    coupons_found.append(coupon_info)
                    logger.info(f"Cupón encontrado: {coupon_info['codigo']} - Tienda: {coupon_info['tienda']} (confianza: {confidence:.2f})")

            return coupons_found

        except Exception as e:
            logger.error(f"Error procesando email: {e}")
            return []

    def scan_emails(self, max_emails=None):
        logger.info("Iniciando escaneo de correos...")
        success, message = self.authenticate()
        if not success:
            return [], message, "auth"

        try:
            if max_emails is None:
                max_emails = self.db.get_max_emails()

            query = self.db.get_search_query()
            emails = self.search_emails(query=query, max_results=max_emails)
            all_coupons = []
            rows_to_insert = []
            processed_codes = set()

            for i, email in enumerate(emails, 1):
                coupons = self.process_email(email['id'])
                for coupon in coupons:
                    code = coupon['codigo']
                    if code not in processed_codes and not self.db.notification_exists(code):
                        processed_codes.add(code)
                        rows_to_insert.append((
                            code,
                            coupon['tienda'],
                            coupon['url'],
                            coupon.get('descuento'),
                            coupon.get('confianza', 0.5),
                            coupon.get('contexto'),
                            coupon.get('id_correo'),
                            coupon.get('asunto'),
                            coupon.get('fecha'),
                            coupon.get('fecha_expiracion')
                        ))
                        all_coupons.append(coupon)

            if rows_to_insert:
                self.db.add_notifications_bulk(rows_to_insert)

            self.db.update_last_scan()
            logger.info(f"Escaneo completado. Encontrados {len(all_coupons)} cupones nuevos")

            return all_coupons, f"Encontrados {len(all_coupons)} cupones nuevos", None

        except Exception as e:
            logger.error(f"Error en escaneo: {e}")
            return [], f"Error: {str(e)[:100]}", "network"
