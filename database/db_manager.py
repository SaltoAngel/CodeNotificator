import sqlite3
import logging

DEFAULT_SEARCH_QUERY = ''
DEFAULT_SEARCH_KEYWORDS = ''
DEFAULT_MAX_EMAILS = 15

logger = logging.getLogger('CouponNotifier')


class DatabaseManager:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()

        # Tabla principal de notificaciones
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                cupon TEXT NOT NULL, 
                tienda TEXT NOT NULL,
                URL TEXT NOT NULL, 
                asunto TEXT,
                descuento TEXT,
                contexto TEXT,
                id_correo TEXT,
                estado TEXT DEFAULT 'nuevo',
                usuario_valido INTEGER DEFAULT 0,
                es_valido INTEGER DEFAULT 1,
                confianza REAL DEFAULT 0.5,
                Fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabla de configuración
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                intervalo_minutos INTEGER DEFAULT 30,
                client_id TEXT,
                client_secret TEXT,
                refresh_token TEXT,
                ultimo_escaneo DATETIME,
                notificaciones_activas INTEGER DEFAULT 1,
                aprendizaje_activo INTEGER DEFAULT 1,
                CHECK (id = 1)
            )
        ''')

        cursor.execute('''
            INSERT OR IGNORE INTO config (id, intervalo_minutos) 
            VALUES (1, 30)
        ''')

        # Verificar y agregar columnas si no existen
        cursor.execute("PRAGMA table_info(config)")
        cols = [row[1] for row in cursor.fetchall()]

        columnas_a_agregar = [
            ('search_query', 'TEXT'),
            ('search_keywords', 'TEXT'),
            ('max_emails', 'INTEGER'),
            ('user_email', 'TEXT')
        ]

        # Verificar y agregar columnas en notifications si no existen
        cursor.execute("PRAGMA table_info(notifications)")
        notif_cols = [row[1] for row in cursor.fetchall()]
        if 'asunto' not in notif_cols:
            cursor.execute("ALTER TABLE notifications ADD COLUMN asunto TEXT")
        if 'contexto' not in notif_cols:
            cursor.execute("ALTER TABLE notifications ADD COLUMN contexto TEXT")
        if 'id_correo' not in notif_cols:
            cursor.execute("ALTER TABLE notifications ADD COLUMN id_correo TEXT")

        for col_name, col_type in columnas_a_agregar:
            if col_name not in cols:
                cursor.execute(f"ALTER TABLE config ADD COLUMN {col_name} {col_type}")

        # Valores por defecto
        cursor.execute(
            "UPDATE config SET search_query = ? WHERE id = 1 AND (search_query IS NULL OR search_query = '')",
            (DEFAULT_SEARCH_QUERY,)
        )
        cursor.execute(
            "UPDATE config SET search_keywords = ? WHERE id = 1 AND (search_keywords IS NULL OR search_keywords = '')",
            (DEFAULT_SEARCH_KEYWORDS,)
        )
        cursor.execute(
            "UPDATE config SET max_emails = ? WHERE id = 1 AND (max_emails IS NULL OR max_emails < 1)",
            (DEFAULT_MAX_EMAILS,)
        )

        # Tabla de aprendizaje simplificada (sin ML)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learning_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tienda TEXT,
                patron TEXT,
                total_apariciones INTEGER DEFAULT 0,
                exitosos INTEGER DEFAULT 0,
                confianza REAL DEFAULT 0.0,
                ultimo_uso DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabla de keywords para aprendizaje contextual
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS keyword_weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tienda TEXT,
                keyword TEXT,
                total_apariciones INTEGER DEFAULT 0,
                exitosos INTEGER DEFAULT 0,
                peso REAL DEFAULT 0.0,
                ultimo_uso DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tienda, keyword)
            )
        ''')

        # Tabla de feedback del usuario
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cupon_id INTEGER,
                cupon_text TEXT,
                tienda TEXT,
                es_valido BOOLEAN,
                comentario TEXT,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cupon_id) REFERENCES notifications(id)
            )
        ''')

        # Tabla de falsos positivos (blacklist dinámica)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS false_positive_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT UNIQUE,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabla de cupones confirmados (whitelist de códigos)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS positive_coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                tienda TEXT,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        self.conn.commit()
        logger.info("Tablas de base de datos creadas/verificadas")

    def get_config(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM config WHERE id = 1")
        return cursor.fetchone()

    def update_config(self, **kwargs):
        if not kwargs:
            return

        set_clause = []
        values = []

        for key, value in kwargs.items():
            if value is not None:
                set_clause.append(f"{key} = ?")
                values.append(value)

        if set_clause:
            query = f"UPDATE config SET {', '.join(set_clause)} WHERE id = 1"
            cursor = self.conn.cursor()
            cursor.execute(query, values)
            self.conn.commit()
            logger.info(f"Configuración actualizada: {', '.join(kwargs.keys())}")

    def update_last_scan(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE config SET ultimo_escaneo = CURRENT_TIMESTAMP WHERE id = 1"
        )
        self.conn.commit()

    def add_notification(self, cupon, tienda, url, descuento=None, confianza=0.5, contexto=None, id_correo=None, asunto=None, fecha=None):
        cursor = self.conn.cursor()
        if fecha:
            cursor.execute(
                "INSERT INTO notifications (cupon, tienda, URL, descuento, confianza, contexto, id_correo, asunto, Fecha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cupon, tienda, url, descuento, confianza, contexto, id_correo, asunto, fecha)
            )
        else:
            cursor.execute(
                "INSERT INTO notifications (cupon, tienda, URL, descuento, confianza, contexto, id_correo, asunto) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cupon, tienda, url, descuento, confianza, contexto, id_correo, asunto)
            )
        self.conn.commit()
        logger.info(f"Cupón agregado: {cupon} - {tienda} (confianza: {confianza:.2f})")
        return cursor.lastrowid

    def add_notifications_bulk(self, rows):
        if not rows:
            return 0
        cursor = self.conn.cursor()
        cursor.executemany(
            "INSERT INTO notifications (cupon, tienda, URL, descuento, confianza, contexto, id_correo, asunto, Fecha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        self.conn.commit()
        return cursor.rowcount

    def notification_exists(self, cupon):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM notifications WHERE cupon = ? LIMIT 1",
            (cupon,)
        )
        return cursor.fetchone() is not None

    def get_notifications(self, limit=100, estado=None, min_confidence=None, days=None, tienda=None, usuario_valido=None, es_valido=None):
        query = "SELECT id, cupon, tienda, asunto, URL, descuento, estado, usuario_valido, es_valido, confianza, Fecha FROM notifications"
        params = []

        where_clauses = []
        if estado:
            where_clauses.append("estado = ?")
            params.append(estado)

        if min_confidence is not None:
            where_clauses.append("confianza >= ?")
            params.append(min_confidence)

        if days is not None:
            where_clauses.append("Fecha >= datetime('now', ?)")
            params.append(f"-{int(days)} days")

        if tienda:
            where_clauses.append("LOWER(tienda) LIKE ?")
            params.append(f"%{tienda.lower()}%")

        if usuario_valido is not None:
            where_clauses.append("usuario_valido = ?")
            params.append(1 if usuario_valido else 0)

        if es_valido is not None:
            where_clauses.append("es_valido = ?")
            params.append(1 if es_valido else 0)

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY Fecha DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    def obtener_id_gmail_por_db(self, id_db):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id_correo FROM notifications WHERE id = ?", (id_db,))
        row = cursor.fetchone()
        return row[0] if row else None

    def get_search_query(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT search_query FROM config WHERE id = 1")
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
        return DEFAULT_SEARCH_QUERY

    def get_max_emails(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT max_emails FROM config WHERE id = 1")
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return max(int(row[0]), 1)
            except:
                return DEFAULT_MAX_EMAILS
        return DEFAULT_MAX_EMAILS

    def update_cupon_validity(self, cupon_id, es_valido, confianza=1.0):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE notifications SET es_valido = ?, usuario_valido = 1, confianza = ? WHERE id = ?",
            (1 if es_valido else 0, confianza, cupon_id)
        )
        self.conn.commit()
        logger.info(f"Cupón {cupon_id} marcado como {'válido' if es_valido else 'inválido'}")

    def add_user_feedback(self, cupon_id, cupon_text, tienda, es_valido, comentario=""):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO user_feedback (cupon_id, cupon_text, tienda, es_valido, comentario)
            VALUES (?, ?, ?, ?, ?)
        ''', (cupon_id, cupon_text, tienda, 1 if es_valido else 0, comentario))
        self.conn.commit()
        return cursor.lastrowid

    def update_learning_pattern(self, tienda, patron, es_valido):
        cursor = self.conn.cursor()

        # Buscar patrón existente
        cursor.execute('''
            SELECT id, total_apariciones, exitosos 
            FROM learning_patterns 
            WHERE tienda = ? AND patron = ?
        ''', (tienda, patron))

        row = cursor.fetchone()

        if row:
            # Actualizar existente
            pattern_id, total, exitosos = row
            total += 1
            exitosos += 1 if es_valido else 0
            confianza = exitosos / total if total > 0 else 0.0

            cursor.execute('''
                UPDATE learning_patterns 
                SET total_apariciones = ?, exitosos = ?, confianza = ?, ultimo_uso = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (total, exitosos, confianza, pattern_id))
        else:
            # Insertar nuevo
            confianza = 1.0 if es_valido else 0.0
            exitosos = 1 if es_valido else 0

            cursor.execute('''
                INSERT INTO learning_patterns (tienda, patron, total_apariciones, exitosos, confianza)
                VALUES (?, ?, 1, ?, ?)
            ''', (tienda, patron, exitosos, confianza))

        self.conn.commit()

    def get_pattern_confidence(self, tienda, patron):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT confianza FROM learning_patterns 
            WHERE tienda = ? AND patron = ?
        ''', (tienda, patron))

        row = cursor.fetchone()
        return row[0] if row else 0.5

    def get_learning_stats(self):
        cursor = self.conn.cursor()

        # Estadísticas de feedback
        cursor.execute('''
            SELECT 
                COUNT(*) as total_feedback,
                SUM(CASE WHEN es_valido = 1 THEN 1 ELSE 0 END) as validos,
                COUNT(DISTINCT tienda) as tiendas_aprendidas
            FROM user_feedback
        ''')
        feedback_stats = cursor.fetchone()

        # Estadísticas de patrones
        cursor.execute('''
            SELECT 
                COUNT(*) as total_patrones,
                AVG(confianza) as confianza_promedio
            FROM learning_patterns
        ''')
        pattern_stats = cursor.fetchone()

        return feedback_stats, pattern_stats

    def update_keyword_weight(self, tienda, keyword, es_valido):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, total_apariciones, exitosos FROM keyword_weights
            WHERE tienda = ? AND keyword = ?
        ''', (tienda, keyword))

        row = cursor.fetchone()
        if row:
            kw_id, total, exitosos = row
            total += 1
            exitosos += 1 if es_valido else 0
            peso = exitosos / total if total > 0 else 0.0
            cursor.execute('''
                UPDATE keyword_weights
                SET total_apariciones = ?, exitosos = ?, peso = ?, ultimo_uso = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (total, exitosos, peso, kw_id))
        else:
            total = 1
            exitosos = 1 if es_valido else 0
            peso = exitosos / total if total > 0 else 0.0
            cursor.execute('''
                INSERT INTO keyword_weights (tienda, keyword, total_apariciones, exitosos, peso)
                VALUES (?, ?, ?, ?, ?)
            ''', (tienda, keyword, total, exitosos, peso))

        self.conn.commit()

    def get_keyword_weights(self, tienda, keywords):
        if not keywords:
            return {}
        cursor = self.conn.cursor()
        placeholders = ",".join(["?"] * len(keywords))
        params = [tienda] + list(keywords)
        cursor.execute(f'''
            SELECT keyword, peso FROM keyword_weights
            WHERE tienda = ? AND keyword IN ({placeholders})
        ''', params)
        return {row[0]: row[1] for row in cursor.fetchall()}

    def get_top_patterns(self, limit=10):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT tienda, patron, confianza, total_apariciones
            FROM learning_patterns 
            WHERE total_apariciones > 0
            ORDER BY confianza DESC, total_apariciones DESC
            LIMIT ?
        ''', (limit,))
        return cursor.fetchall()

    def get_false_positive_terms(self, limit=200):
        cursor = self.conn.cursor()
        cursor.execute("SELECT term FROM false_positive_terms ORDER BY term ASC LIMIT ?", (limit,))
        return [row[0] for row in cursor.fetchall()]

    def remove_false_positive_term(self, term):
        if not term:
            return
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM false_positive_terms WHERE term = ?", (term.upper(),))
        self.conn.commit()

    def add_false_positive_term(self, term):
        if not term:
            return
        cursor = self.conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO false_positive_terms (term) VALUES (?)", (term.upper(),))
        self.conn.commit()

    def is_false_positive_term(self, term):
        if not term:
            return False
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM false_positive_terms WHERE term = ? LIMIT 1", (term.upper(),))
        return cursor.fetchone() is not None

    def get_positive_coupons(self, limit=200):
        cursor = self.conn.cursor()
        cursor.execute("SELECT code, tienda FROM positive_coupons ORDER BY code ASC LIMIT ?", (limit,))
        return cursor.fetchall()

    def remove_positive_coupon(self, code):
        if not code:
            return
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM positive_coupons WHERE code = ?", (code.upper(),))
        self.conn.commit()

    def add_positive_coupon(self, code, tienda=None):
        if not code:
            return
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO positive_coupons (code, tienda) VALUES (?, ?)",
            (code.upper(), tienda)
        )
        self.conn.commit()

    def is_positive_coupon(self, code):
        if not code:
            return False
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM positive_coupons WHERE code = ? LIMIT 1", (code.upper(),))
        return cursor.fetchone() is not None

    def close(self):
        self.conn.close()
        logger.info("Conexión a base de datos cerrada")
