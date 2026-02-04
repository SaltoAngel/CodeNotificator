from textblob import TextBlob
import re

class ContextAnalyzer:
    def __init__(self):
        # Palabras clave de urgencia
        self.urgency_keywords = [
            'hoy', 'today', 'now', 'ahora', 'expira', 'expire', 'último día', 'last day', 
            'horas', 'hours', 'limited', 'limitado', 'ya', 'hurry', 'corre', 'finaliza'
        ]
        
        # Palabras clave promocionales (refuerzo positivo)
        self.promo_keywords = [
            'regalo', 'gift', 'free', 'gratis', 'off', 'save', 'ahorra', 'descuento', 
            'exclusive', 'exclusivo', 'bday', 'cumpleaños', 'special', 'especial'
        ]
        
        # Palabras que sugieren spam o newsletters informativas (refuerzo negativo)
        self.spam_keywords = [
            'noticias', 'news', 'update', 'actualización', 'resumen', 'summary', 
            'terminos', 'terms', 'politica', 'policy', 'recibo', 'receipt'
        ]

    def analyze(self, subject, snippet):
        """
        Analiza el texto y devuelve un diccionario con métricas de NLP.
        """
        text = f"{subject} {snippet}"
        try:
            blob = TextBlob(text)
            
            # 1. Análisis de Sentimiento
            # Polarity: -1 (negativo) a 1 (positivo). Las ofertas suelen ser muy positivas (>0.3)
            # Subjectivity: 0 (objetivo) a 1 (subjetivo). Marketing es subjetivo (>0.4)
            polarity = blob.sentiment.polarity
            subjectivity = blob.sentiment.subjectivity
            
            # 2. Score de Promoción (0 a 1)
            promo_score = self._calculate_keyword_score(text, self.promo_keywords)
            spam_score = self._calculate_keyword_score(text, self.spam_keywords)
            
            # 3. Urgencia
            is_urgent = any(re.search(r'\b' + re.escape(w) + r'\b', text, re.IGNORECASE) for w in self.urgency_keywords)
            
            # 4. Cálculo final de relevancia
            # Base score usa polaridad y subjetividad porque el marketing es emoción pura
            final_relevance = (polarity * 0.4) + (subjectivity * 0.3) + (promo_score * 0.5) - (spam_score * 0.6)
            
            # Normalizar entre 0 y 1
            final_relevance = max(0.0, min(1.0, final_relevance))
            
            return {
                'relevance_score': round(final_relevance, 2),
                'is_urgent': is_urgent,
                'sentiment': 'positive' if polarity > 0.2 else 'neutral/negative',
                'keywords_found': promo_score > 0
            }
            
        except Exception as e:
            # Fallback seguro en caso de error de TextBlob
            return {'relevance_score': 0.5, 'is_urgent': False, 'error': str(e)}

    def _calculate_keyword_score(self, text, keywords):
        count = sum(1 for w in keywords if re.search(r'\b' + re.escape(w) + r'\b', text, re.IGNORECASE))
        # Cap en 1.0 (si tiene 3 palabras clave ya es muy relevante)
        return min(1.0, count * 0.35)
