import logging
import re

logger = logging.getLogger('CouponNotifier')


class SimpleLearningSystem:
    def __init__(self, db_manager):
        self.db = db_manager
        self.pattern_cache = {}
        self.stopwords = set([
            "EL", "LA", "LOS", "LAS", "DE", "DEL", "Y", "EN", "POR", "PARA",
            "CON", "SIN", "UN", "UNA", "UNOS", "UNAS", "A", "AL", "O",
            "OF", "THE", "AND", "FOR", "WITH", "FROM", "YOUR", "THIS", "THAT",
            "ES", "SON", "ESTA", "ESTE", "HASTA", "VÁLIDO", "VALIDO",
        ])

    def extract_pattern(self, texto_cupon):
        """Extrae un patrón simplificado del código del cupón"""
        texto = texto_cupon.upper().strip()

        # Detectar tipo de patrón
        if '-' in texto:
            parts = texto.split('-')
            if len(parts) == 2:
                return "XXXX-XXXX"
            elif len(parts) == 3:
                return "XXXX-XXXX-XXXX"
            else:
                return "MULTI-HYPHEN"

        # Verificar patrones comunes
        if re.match(r'^[A-Z]{2}\d{6}$', texto):
            return "LLDDDDDD"  # 2 letras + 6 dígitos
        elif re.match(r'^\d{4}[A-Z]{3}$', texto):
            return "DDDDLLL"   # 4 dígitos + 3 letras
        elif re.match(r'^[A-Z]{3}\d{5}$', texto):
            return "LLLDDDDD"  # 3 letras + 5 dígitos
        elif re.match(r'^[A-Z]{4}\d{4}$', texto):
            return "LLLLDDDD"  # 4 letras + 4 dígitos
        elif re.match(r'^\d{8}$', texto):
            return "DDDDDDDD"  # 8 dígitos
        elif re.match(r'^[A-Z]{8}$', texto):
            return "LLLLLLLL"  # 8 letras
        elif re.match(r'^[A-Z0-9]{8}$', texto):
            return "ALPHANUM8"  # 8 caracteres alfanuméricos
        elif re.match(r'^[A-Z0-9]{10}$', texto):
            return "ALPHANUM10"  # 10 caracteres alfanuméricos

        # Patrón por longitud y composición
        length = len(texto)
        digit_count = sum(1 for c in texto if c.isdigit())
        letter_count = sum(1 for c in texto if c.isalpha())

        if digit_count == 0:
            return f"ALL_LETTERS_{length}"
        elif letter_count == 0:
            return f"ALL_DIGITS_{length}"
        else:
            ratio = digit_count / max(letter_count, 1)
            if ratio > 2:
                return f"MOSTLY_DIGITS_{length}"
            elif ratio < 0.5:
                return f"MOSTLY_LETTERS_{length}"
            else:
                return f"MIXED_{length}"

    def calculate_confidence(self, texto_cupon, tienda=None, contexto=None):
        """Calcula confianza basada en reglas heurísticas y aprendizaje previo"""
        texto = texto_cupon.upper().strip()

        # Reglas básicas de validez
        if len(texto) < 4 or len(texto) > 25:
            return 0.1  # Muy baja confianza

        has_digit = any(c.isdigit() for c in texto)
        has_letter = any(c.isalpha() for c in texto)

        if not (has_digit and has_letter):
            return 0.2  # Baja confianza

        # Patrones comunes de cupones válidos
        common_valid_patterns = [
            r'^[A-Z0-9]{4,}$',
            r'^[A-Z0-9]{4,}-[A-Z0-9]{4,}$',
            r'^[A-Z]{2,}\d{3,}[A-Z]{0,3}$',
            r'^\d{4,}[A-Z]{2,}$',
            r'^[A-Z0-9]{8,12}$',
        ]

        for pattern in common_valid_patterns:
            if re.match(pattern, texto):
                base_confidence = 0.7
                break
        else:
            base_confidence = 0.4

        # Ajustar por tienda específica si tenemos datos
        if tienda:
            patron = self.extract_pattern(texto)
            learned_confidence = self.db.get_pattern_confidence(tienda, patron)

            # Combinar confianza aprendida con reglas heurísticas
            if learned_confidence > 0:
                # Ponderar: 70% aprendizaje, 30% reglas heurísticas
                final_confidence = (learned_confidence * 0.7) + (base_confidence * 0.3)
                base_confidence = min(max(final_confidence, 0.1), 0.95)

        # Ajuste por keywords contextuales si hay contexto
        if contexto and tienda:
            keywords = self.extract_keywords(contexto)
            weights = self.db.get_keyword_weights(tienda, keywords)
            if weights:
                avg_weight = sum(weights.values()) / max(len(weights), 1)
                base_confidence = min(max(base_confidence + (avg_weight * 0.2), 0.1), 0.98)

        return base_confidence

    def extract_keywords(self, contexto, max_keywords=8):
        """Extrae keywords simples del contexto cercano al cupón."""
        if not contexto:
            return []
        words = re.findall(r"[A-ZÁÉÍÓÚÜÑ]{4,}", contexto.upper())
        filtered = [w for w in words if w not in self.stopwords]
        # Priorizar palabras más largas (más informativas)
        filtered.sort(key=len, reverse=True)
        return filtered[:max_keywords]

    def learn_from_feedback(self, cupon_text, tienda, es_valido, contexto=None):
        """Aprende del feedback del usuario"""
        patron = self.extract_pattern(cupon_text)

        # Actualizar en base de datos
        self.db.update_learning_pattern(tienda, patron, es_valido)

        # Aprender keywords del contexto si el cupón fue válido
        if contexto:
            keywords = self.extract_keywords(contexto)
            for kw in keywords:
                self.db.update_keyword_weight(tienda, kw, es_valido)

        # Actualizar caché
        cache_key = f"{tienda}_{patron}"
        if cache_key not in self.pattern_cache:
            self.pattern_cache[cache_key] = {'total': 0, 'success': 0}

        self.pattern_cache[cache_key]['total'] += 1
        if es_valido:
            self.pattern_cache[cache_key]['success'] += 1

        logger.info(f"Aprendizaje: {cupon_text} -> {patron} ({'válido' if es_valido else 'inválido'})")

    def get_stats(self):
        """Obtiene estadísticas del sistema de aprendizaje"""
        feedback_stats, pattern_stats = self.db.get_learning_stats()

        return {
            'total_feedback': feedback_stats[0] if feedback_stats else 0,
            'valid_feedback': feedback_stats[1] if feedback_stats else 0,
            'stores_learned': feedback_stats[2] if feedback_stats else 0,
            'total_patterns': pattern_stats[0] if pattern_stats else 0,
            'avg_confidence': pattern_stats[1] if pattern_stats else 0.0,
        }
