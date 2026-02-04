import re
import json
import os
import sys
import validators
from datetime import datetime, timedelta

# Añadir el directorio raíz al PATH para permitir ejecuciones directas
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import logger
from core.ocr_engine import apply_fuzzy_correction

class CouponExtractor:
    def __init__(self, db_manager, learning_system=None):
        self.db = db_manager
        self.learning_system = learning_system
        self.positive_context_keywords = [
            "CODE", "CODIGO", "CÓDIGO", "CUPON", "CUPÓN", "PROMO", "PROMOCION",
            "DESCUENTO", "OFF", "%", "REBATE", "DEAL", "AHORRA", "SAVE", "DISCOUNT", "VOUCHER"
        ]
        self.negative_context_keywords = [
            "TRACKING", "SEGUIMIENTO", "ENVIO", "ENVÍO", "SHIPMENT", "ORDER",
            "PEDIDO", "FACTURA", "INVOICE", "GUIA", "GUÍA", "SUPPORT", "AYUDA"
        ]
        self.blacklist_words = {
            "PROMOC0DE", "PROMOCODE", "VALID", "EXPIRED", "CLICKHERE",
            "SHOPNOW", "VIEWONLINE", "UNSUBSCRIBE", "PRIVACY", "TERMS", 
            "CONDICIONES", "YOURFIRSTYEAR", "YOURFIRSTORDER", "SIGNUP",
            "ORDER", "ODER", "TRACKING", "SHIPMENT", "INVOICE", "RECEIPT",
            "SUBTOTAL", "TOTAL", "SUMMARY", "RESUMEN", "PAGO", "PAYMENT",
            "PHONE", "TELEFONO", "TEL", "FAX", "MOBILE", "CELULAR",
            "ADDRESS", "DIRECCION", "CALLE", "STREET", "AVENUE", "SUITE",
            "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
            "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
            "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
            "COPYRIGHT", "RIGHTS", "RESERVED", "DERECHOS", "RESERVADOS",
            "SUPPORT", "AYUDA", "CONTACT", "CONTACTO", "QUESTIONS", "PREGUNTAS",
            "WHATSAPP", "FACEBOOK", "INSTAGRAM", "TWITTER", "YOUTUBE",
            "LOGIN", "INICIAR", "SESION", "ACCOUNT", "CUENTA", "PASSWORD",
            "IMG", "JPG", "PNG", "GIF", "HTML", "PHP", "ASPX",
            "DOCTYPE", "XHTML", "HEAD", "BODY", "DIV", "SPAN", "SECTION",
            "ARTICLE", "HEADER", "FOOTER", "NAV", "ASIDE", "MAIN",
            "SCRIPT", "STYLE", "META", "LINK", "IFRAME", "SVG", "PATH",
            "VIEWBOX", "HREF", "SRC", "ALT", "TITLE", "CLASS", "ID",
            "WIDTH", "HEIGHT", "ARIA", "DATA", "REL", "TARGET", "BUTTON",
            "TRACK", "SEGUIR", "PAQUETE", "PACKAGE", "DELIVERY", "ENTREGA"
        }
        self.months_map = {
            'JANUARY': 1, 'JAN': 1, 'FEBRUARY': 2, 'FEB': 2, 'MARCH': 3, 'MAR': 3,
            'APRIL': 4, 'APR': 4, 'MAY': 5, 'JUNE': 6, 'JUN': 6, 'JULY': 7, 'JUL': 7,
            'AUGUST': 8, 'AUG': 8, 'SEPTEMBER': 9, 'SEP': 9, 'OCTOBER': 10, 'OCT': 10,
            'NOVEMBER': 11, 'NOV': 11, 'DECEMBER': 12, 'DEC': 12,
            'ENERO': 1, 'FEBRERO': 2, 'MARZO': 3, 'ABRIL': 4, 'MAYO': 5, 'JUNIO': 6,
            'JULIO': 7, 'AGOSTO': 8, 'SEPTIEMBRE': 9, 'OCTUBRE': 10, 'NOVIEMBRE': 11, 'DICIEMBRE': 12
        }
        self.min_score_threshold = 0.30
        try:
            config = self.db.get_config() or {}
            threshold = config.get('score_threshold', 0.30)
            if threshold:
                self.min_score_threshold = float(threshold)
        except Exception:
            pass

    def extraer_cupon_inteligente(self, email_data):
        """
        Estrategia de Cascada (Waterfall Strategy):
        Intenta los métodos más rápidos y precisos primero.
        """
        tienda = email_data.get('tienda', 'Desconocida')
        
        # 1. INTENTO RAPIDÍSIMO: Metadatos de Gmail (JSON-LD)
        code, desc = self.extract_from_metadata(email_data.get('body_html', ''))
        if code:
            logger.info(f"✨ Cupón encontrado en Metadatos JSON-LD: {code}")
            return {
                'codigo': code,
                'tienda': tienda,
                'descuento': desc or "Descuento en Metadatos",
                'confianza': 1.0,
                'score': 1.0,
                'metodo': 'Alta (Metadata)'
            }

        # 2. INTENTO RÁPIDO: Regex en el texto plano
        if email_data.get('body_text'):
            info = self.extract_coupon_info(email_data['body_text'], tienda)
            if info['codigo'] and info['codigo'] != '[OFERTA DIRECTA]':
                logger.info(f"🔍 Cupón encontrado via Regex: {info['codigo']}")
                return {
                    'codigo': info['codigo'],
                    'tienda': info['tienda'],
                    'descuento': info['descuento'],
                    'confianza': info.get('confianza_contexto', 0.8),
                    'score': info.get('score', info.get('confianza_contexto', 0.8)),
                    'metodo': 'Media (Regex)',
                    'fecha_expiracion': info.get('fecha_expiracion'),
                    'url': info.get('url')
                }

        # 3. FALLBACK: Si llegamos aquí, se requiere OCR (se maneja en gmail_engine)
        return None

    def extract_from_metadata(self, html_content):
        if not html_content:
            return None, None
            
        try:
            # Buscar bloques <script type="application/ld+json">
            json_ld_matches = re.findall(r'<script type="application/ld\+json">([\s\S]*?)</script>', html_content)
            
            for json_str in json_ld_matches:
                try:
                    data = json.loads(json_str)
                    if isinstance(data, list):
                        for item in data:
                            code, desc = self._parse_json_ld_item(item)
                            if code: return code, desc
                    else:
                        code, desc = self._parse_json_ld_item(data)
                        if code: return code, desc
                except:
                    continue
        except Exception as e:
            logger.error(f"Error en extract_from_metadata: {e}")
            
        return None, None

    def _parse_json_ld_item(self, item):
        # Gmail y merchants suelen usar 'promoCode' o dentro de una 'Promotion'
        if 'promoCode' in item:
            return str(item['promoCode']), item.get('description')
        
        # Estructuras comunes de Schema.org
        if item.get('@type') in ['Offer', 'Promotion']:
            if 'discountCode' in item:
                return str(item['discountCode']), item.get('description')
                
        return None, None

    def extract_coupon_info(self, text, tienda_from_sender=None, known_terms=None):
        info = {
            'codigo': '',
            'tienda': 'Desconocida',
            'url': '',
            'descuento': '',
            'contexto': text[:300],
            'fecha_expiracion': None
        }

        # Corrección Difusa si se detecta que es texto de OCR (muchas inconsistencias)
        if known_terms and (len(re.findall(r'[A-Z]', text)) < len(re.findall(r'[0-9]', text)) * 0.5):
            text = apply_fuzzy_correction(text, known_terms)

        if tienda_from_sender and tienda_from_sender != 'Desconocida':
            info['tienda'] = tienda_from_sender

        percent_values = re.findall(r'(\d{1,3}(?:[,.]\d{1,2})?)%', text)
        if percent_values:
            info['descuento'] = f"{max(percent_values, key=lambda x: float(x.replace(',', '.')))}%"

        money_matches = []
        money_patterns = [
            r'(?i)(?:USD|US\$|\$)\s*(\d{1,4}(?:[,.]\d{1,2})?)',
            r'(?i)(\d{1,4}(?:[,.]\d{1,2})?)\s*(?:USD|US\$|DOLARES|DÓLARES|DOLLARS)',
            r'(?i)(\d{1,4}(?:[,.]\d{1,2})?)\s*\$'
        ]
        for pattern in money_patterns:
            for m in re.finditer(pattern, text):
                val = m.group(1).replace(',', '.')
                money_matches.append({
                    'val': f"${val}",
                    'start': m.start(1)
                })

        url_spans = []
        for m in re.finditer(r'https?://[^\s]+', text):
            url_spans.append((m.start(), m.end(), m.group(0)))

        patterns = [
            r'(?:codigo|código|cup[oó]n|promo|coupon|code)[:\s\-]*([A-Z0-9\-]{6,20})',
            r'(?:with the code|using code)[:\s\-]*([A-Z0-9\-]{6,20})',
            r'([A-Z0-9]{4,}[-\s]?[A-Z0-9]{4,}[-\s]?[A-Z0-9]{4,})',
            r'CODE\s*[:=]?\s*([A-Z0-9\-]+)',
            r'([A-Z]{2,}\d{3,}[A-Z]{0,3})',
            r'(\d{4,}[A-Z]{2,})',
            r'\b([A-Z]{4,12})\b',
        ]

        candidates = []
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                code_raw = match.group(1).upper().strip()
                code = re.sub(r'\s+', '', code_raw)

                start_idx, end_idx = match.start(1), match.end(1)
                if any(start_idx >= s and end_idx <= e for s, e, _ in url_spans):
                    continue

                best_variant = self._select_best_variant(code, info['tienda'], text, start_idx, end_idx)
                if best_variant:
                    best_code, final_conf = best_variant
                    candidates.append({
                        'code': best_code,
                        'confidence': final_conf,
                        'start': start_idx,
                        'end': end_idx
                    })

        if candidates:
            candidates.sort(key=lambda c: (c['confidence'], len(c['code'])), reverse=True)
            best = candidates[0]
            info['codigo'] = best['code']
            info['confianza_contexto'] = best['confidence']
            info['score'] = best['confidence']

            # Refinar descuento por proximidad al mejor código
            desc_matches_with_pos = []
            for m in re.finditer(r'(\d{1,3}(?:[,.]\d{1,2})?)%', text):
                desc_matches_with_pos.append({
                    'val': f"{m.group(1)}%",
                    'dist': abs(m.start() - best['start'])
                })
            for item in money_matches:
                desc_matches_with_pos.append({
                    'val': item['val'],
                    'dist': abs(item['start'] - best['start'])
                })
            
            if desc_matches_with_pos:
                close_descs = [d for d in desc_matches_with_pos if d['dist'] < 120]
                if close_descs:
                    info['descuento'] = min(close_descs, key=lambda x: x['dist'])['val']
                elif percent_values:
                    info['descuento'] = f"{max(percent_values, key=lambda x: float(x.replace(',', '.')))}%"
                elif money_matches:
                    info['descuento'] = max(money_matches, key=lambda x: float(x['val'].replace('$', '')))['val']
        elif percent_values or money_matches:
            # Si no hay código pero sí descuentos, capturamos como oferta directa
            info['codigo'] = '[OFERTA DIRECTA]'
            info['confianza_contexto'] = 0.4 # Confianza baja por ser incompleto
            info['score'] = 0.4
            if percent_values:
                info['descuento'] = f"{max(percent_values, key=lambda x: float(x.replace(',', '.')))}%"
            else:
                info['descuento'] = max(money_matches, key=lambda x: float(x['val'].replace('$', '')))['val']

        valid_urls = []
        for _, _, url in url_spans:
            cleaned = self._clean_url(url)
            if cleaned:
                valid_urls.append(cleaned)

        if valid_urls:
            info['url'] = valid_urls[0]

        if info['tienda'] == 'Desconocida':
            tiendas = {
                'amazon': ['amazon', 'amzn'],
                'mercado libre': ['mercado libre', 'mercadolibre', 'ml'],
                'ebay': ['ebay'],
                'aliexpress': ['aliexpress'],
                'walmart': ['walmart'],
            }

            text_lower = text.lower()
            for tienda, keywords in tiendas.items():
                if any(keyword in text_lower for keyword in keywords):
                    info['tienda'] = tienda.title()
                    break

        # Extraer fecha de expiración
        info['fecha_expiracion'] = self._extract_expiration_date(text)

        return info

    def is_valid_coupon_code(self, code, tienda=None):
        if not code or len(code) < 4 or len(code) > 25:
            return False

        if not self._validate_by_brand(code, tienda):
            return False

        if self.learning_system:
            confidence = self.learning_system.calculate_confidence(code, tienda)
            return confidence > 0.5

        has_digit = any(c.isdigit() for c in code)
        has_letter = any(c.isalpha() for c in code)

        if not (has_digit and has_letter):
            return False

        patterns = [
            r'^[A-Z0-9]{4,}$',
            r'^[A-Z0-9]{4,}-[A-Z0-9]{4,}$',
            r'^[A-Z]{2,}\d{3,}[A-Z]{0,3}$',
            r'^\d{4,}[A-Z]{2,}$',
        ]

        return any(re.match(pattern, code, re.IGNORECASE) for pattern in patterns)

    def _select_best_variant(self, code, tienda, text, start_idx, end_idx):
        best_code = None
        best_conf = -1

        for variant in self._generate_ocr_variants(code, tienda=tienda):
            if self._is_blacklisted(variant):
                continue
            if self.db.is_false_positive_term(variant):
                continue
            if len(variant) > 15:
                continue
            if self._has_url_context(text, start_idx, end_idx):
                continue
            if variant.isalpha() and not self._has_hot_context(text, start_idx, end_idx):
                continue

            if variant.isalpha() and self._has_hot_context(text, start_idx, end_idx):
                is_valid = True
            else:
                is_valid = self.is_valid_coupon_code(variant, tienda)
            if not is_valid:
                continue

            base_conf = 0.6
            if self.learning_system:
                base_conf = self.learning_system.calculate_confidence(variant, tienda, contexto=text)

            context_score = self._calculate_context_score(text, start_idx, end_idx, code=variant)
            rule_score = self.score_candidate(variant, text, start_idx, end_idx)

            # Normalizar y ponderar para evitar saturación
            base_norm = min(max(base_conf, 0.0), 1.0)
            context_norm = min(max((context_score + 1.0) / 2.0, 0.0), 1.0)
            rule_norm = min(max((rule_score + 1.0) / 2.0, 0.0), 1.0)

            final_conf = (base_norm * 0.5) + (context_norm * 0.3) + (rule_norm * 0.2)

            if final_conf < self.min_score_threshold:
                continue

            if self.db.is_positive_coupon(variant):
                final_conf = min(final_conf + 0.25, 0.99)

            if final_conf > best_conf or (final_conf == best_conf and (best_code is None or len(variant) > len(best_code))):
                best_conf = final_conf
                best_code = variant

        if best_code is None:
            return None

        return best_code, best_conf

    def score_candidate(self, candidate, text, start_idx, end_idx):
        """Score adicional basado en reglas. Retorna un valor entre -1.0 y 1.0."""
        score = 0.0
        cand_up = candidate.upper()

        # Filtro radical: blacklist
        if self._is_blacklisted(cand_up) or len(candidate) < 3:
            return -1.0

        # Bonus por mayúsculas
        if candidate.isupper():
            score += 0.10

        # Bonus por formato alfanumérico
        if any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate):
            score += 0.30

        # Contexto cercano
        window = 40
        context_start = max(0, start_idx - window)
        context_end = min(len(text), end_idx + window)
        context = text[context_start:context_end].upper()

        if any(k in context for k in self.positive_context_keywords):
            score += 0.40

        if any(k in context for k in self.negative_context_keywords):
            score -= 0.50

        # Limitar rango
        return max(min(score, 1.0), -1.0)

    def set_score_threshold(self, value):
        try:
            value = float(value)
        except Exception:
            return
        if value <= 0:
            return
        self.min_score_threshold = value

    def _generate_ocr_variants(self, code, tienda=None):
        apply_homoglyphs = self._should_apply_homoglyphs(tienda)
        variants = {code}
        if apply_homoglyphs:
            variants.add(self._normalize_homoglyphs(code))
        if '0' in code or 'O' in code:
            variants.add(code.replace('0', 'O'))
            variants.add(code.replace('O', '0'))
        if '1' in code or 'I' in code:
            variants.add(code.replace('1', 'I'))
            variants.add(code.replace('I', '1'))
        # Normalizar homoglifos en todas las variantes si aplica
        if apply_homoglyphs:
            normalized = {self._normalize_homoglyphs(v) for v in variants}
            variants = variants.union(normalized)
        return list(variants)

    def _should_apply_homoglyphs(self, tienda):
        """Aplica corrección O/0 solo si el patrón aprendido es mayormente numérico."""
        if not tienda:
            return False
        try:
            best = self.db.get_best_pattern_for_store(tienda)
            if not best:
                return False
            pattern = best.get('pattern', '')
            if not pattern:
                return False
            numeric_markers = [
                'DD', 'ALL_DIGITS', 'MOSTLY_DIGITS', 'DDDD', 'DDDDDD', 'LLDD', 'DDLL'
            ]
            return any(marker in pattern for marker in numeric_markers)
        except Exception:
            return False

    def _normalize_homoglyphs(self, code):
        """Corrige O/0 cuando hay contexto numérico."""
        if not code:
            return code
        fixed = re.sub(r'(?<=\d)O(?=\d)', '0', code)
        fixed = re.sub(r'(?<=\d)O', '0', fixed)
        fixed = re.sub(r'O(?=\d)', '0', fixed)
        return fixed

    def _is_blacklisted(self, code):
        code_upper = code.upper()
        if self._looks_like_html_noise(code_upper):
            return True
        if code_upper in self.blacklist_words:
            return True
        return any(word in code_upper for word in self.blacklist_words)

    def _looks_like_html_noise(self, code_upper):
        html_tokens = {
            "HTML", "XHTML", "HEAD", "BODY", "DIV", "SPAN", "SECTION", "ARTICLE",
            "HEADER", "FOOTER", "NAV", "ASIDE", "MAIN", "SCRIPT", "STYLE",
            "META", "LINK", "IFRAME", "SVG", "PATH", "VIEWBOX", "HREF",
            "SRC", "ALT", "TITLE", "CLASS", "ID", "WIDTH", "HEIGHT",
            "ARIA", "DATA", "REL", "TARGET", "BUTTON"
        }

        if code_upper in html_tokens:
            return True

        # Atributos típicos: data-*, aria-*
        if code_upper.startswith("DATA") or code_upper.startswith("ARIA"):
            return True

        # Tokens tipo HTML (solo letras, muy cortos, típicos de etiquetas)
        if code_upper.isalpha() and len(code_upper) <= 5 and code_upper in html_tokens:
            return True

        return False

    def _has_url_context(self, text, start_idx, end_idx):
        window = 40
        context_start = max(0, start_idx - window)
        context_end = min(len(text), end_idx + window)
        context = text[context_start:context_end].upper()
        return any(tok in context for tok in ["HTTP", ".COM", "/"])

    def _has_hot_context(self, text, start_idx, end_idx):
        window = 40
        context_start = max(0, start_idx - window)
        context_end = min(len(text), end_idx + window)
        context = text[context_start:context_end].upper()
        if any(k in context for k in self.positive_context_keywords):
            return True
        if "%" in context or "$" in context:
            return True
        return False

    def _calculate_context_score(self, text, start_idx, end_idx, code=""):
        window = 60
        context_start = max(0, start_idx - window)
        context_end = min(len(text), end_idx + window)
        context = text[context_start:context_end].upper()

        score = 0.0
        
        # Bonus por palabras clave positivas (CUPÓN, CODE, etc.)
        if any(k in context for k in self.positive_context_keywords):
            score += 0.40  # +40 pts según estrategia
            
        # Penalización por palabras clave negativas
        if any(k in context for k in self.negative_context_keywords):
            score -= 0.50

        # Bonus por cercanía a indicadores específicos (CODE:, CUPON:)
        if re.search(r'(CODE|CODIGO|CÓDIGO|CUPON|CUPÓN)\s*[:=\-]', context):
            score += 0.10

        # Nueva lógica de puntuación según Estrategia de Cascada
        if code:
            # Penalización por palabras técnicas (VALID, EXPIRED, etc.)
            technicals = ["VALID", "EXPIRED", "ACTIVE", "PROMO", "CODE", "CUPON", "CUPÓN", "APPLIED"]
            if any(t == code.upper() for t in technicals):
                score -= 1.00 # -100 pts: descartar casi seguro
                
            # Bonus por mezcla de letras y números (típico de cupones reales)
            has_digit = any(c.isdigit() for c in code)
            has_letter = any(c.isalpha() for c in code)
            if has_digit and has_letter:
                score += 0.20 # +20 pts

        score += self._word_proximity_bonus(text, start_idx, end_idx)
        return score

    def _word_proximity_bonus(self, text, start_idx, end_idx, max_words=5):
        try:
            words = list(re.finditer(r"[A-ZÁÉÍÓÚÜÑ0-9%$]+", text.upper()))
            if not words:
                return 0.0

            code_word_idx = None
            for i, w in enumerate(words):
                if w.start() <= start_idx <= w.end() or w.start() <= end_idx <= w.end():
                    code_word_idx = i
                    break
                if w.start() > end_idx:
                    break

            if code_word_idx is None:
                return 0.0

            bonus = 0.0
            for i, w in enumerate(words):
                if i == code_word_idx:
                    continue
                if abs(i - code_word_idx) > max_words:
                    continue
                token = w.group(0)
                if token in self.positive_context_keywords or token in ["DTO", "VOUCHER", "CUPON", "CUPÓN"]:
                    bonus += 0.3
                if token in self.negative_context_keywords:
                    bonus -= 0.5
            return bonus
        except Exception:
            return 0.0

    def _validate_by_brand(self, code, tienda=None):
        if not tienda:
            return True
        tienda_norm = tienda.strip().lower()
        if "walmart" in tienda_norm and code.isdigit() and 8 <= len(code) <= 16:
            return self._luhn_check(code)
        return True

    def _luhn_check(self, code):
        if not code.isdigit():
            return False
        total = 0
        reverse_digits = code[::-1]
        for i, d in enumerate(reverse_digits):
            n = int(d)
            if i % 2 == 1:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        return total % 10 == 0

    def _clean_url(self, url):
        if not url:
            return ""
        cleaned = url.strip().strip(')]}>,.;')
        if not cleaned:
            return ""
        if not cleaned.lower().startswith(("http://", "https://")):
            cleaned = "https://" + cleaned
        if validators.url(cleaned):
            return cleaned
        return ""

    def _extract_expiration_date(self, text):
        """Intenta extraer una fecha de expiración del texto."""
        text_upper = text.upper()
        
        # Patrones de fechas comunes
        date_patterns = [
            r'EXPIRES\s+(?:ON\s+)?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)',
            r'VENCE\s+(?:EL\s+)?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)',
            r'VALID\s+(?:UNTIL|THRU)\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)',
            r'VÁLIDO\s+(?:HASTA)\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)',
            r'ENDS\s+ON\s+(\d{1,2}\s+[A-Z]{3,10})', # 31 March
            r'EXPIRES\s+(?:ON\s+)?(\d{1,2}\s+[A-Z]{3,10})',
            r'ENDS\s+IN\s+(\d+)\s+(DAYS|DÍAS)',
            r'TERMINA\s+EN\s+(\d+)\s+(DAYS|DÍAS)'
        ]

        for pattern in date_patterns:
            match = re.search(pattern, text_upper)
            if match:
                groups = match.groups()
                if len(groups) == 1: # Formato fecha
                    try:
                        date_str = groups[0]
                        # Simplificación: asumir año actual si no está
                        if date_str.count('/') == 1:
                            date_str += f"/{datetime.now().year}"
                        
                        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y"):
                            try:
                                return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d %H:%M:%S")
                            except ValueError:
                                continue
                        
                        # Manejo de meses con nombre (e.g. 31 MARCH)
                        month_match = re.match(r'(\d{1,2})\s+([A-Z]+)', date_str)
                        if month_match:
                            d, m_name = month_match.groups()
                            if m_name in self.months_map:
                                m = self.months_map[m_name]
                                y = datetime.now().year
                                # Ajustar año si el mes ya pasó mucho
                                if m < datetime.now().month - 1: y += 1
                                return f"{y:04d}-{m:02d}-{int(d):02d} 23:59:59"

                    except Exception:
                        pass
                elif len(groups) == 2: # Formato "en X días"
                    try:
                        days = int(groups[0])
                        return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass
        
        return None
