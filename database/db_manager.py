import sqlite3
import logging

DEFAULT_SEARCH_QUERY = ''
DEFAULT_SEARCH_KEYWORDS = ''
DEFAULT_MAX_EMAILS = 50

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
            ('user_email', 'TEXT'),
            ('key_valid', 'TEXT'),
            ('key_discard', 'TEXT'),
            ('key_expired', 'TEXT'),
            ('key_scan', 'TEXT')
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
        if 'fecha_expiracion' not in notif_cols:
            cursor.execute("ALTER TABLE notifications ADD COLUMN fecha_expiracion DATETIME")
        if 'usuario_valido' not in notif_cols:
            cursor.execute("ALTER TABLE notifications ADD COLUMN usuario_valido INTEGER DEFAULT 0")
        if 'es_valido' not in notif_cols:
            cursor.execute("ALTER TABLE notifications ADD COLUMN es_valido INTEGER DEFAULT 1")
        if 'confianza' not in notif_cols:
            cursor.execute("ALTER TABLE notifications ADD COLUMN confianza REAL DEFAULT 0.5")
        
        # Asegurar que no haya valores NULL en las nuevas columnas para que el filtrado funcione
        cursor.execute("UPDATE notifications SET usuario_valido = 0 WHERE usuario_valido IS NULL")
        cursor.execute("UPDATE notifications SET es_valido = 1 WHERE es_valido IS NULL")
        cursor.execute("UPDATE notifications SET confianza = 0.5 WHERE confianza IS NULL")

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
        
        # Atajos por defecto si no existen
        defaults = [('key_valid', 'v'), ('key_discard', 'x'), ('key_expired', 'e'), ('key_scan', 's')]
        for col, val in defaults:
            cursor.execute(f"UPDATE config SET {col} = ? WHERE id = 1 AND ({col} IS NULL OR {col} = '')", (val,))

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
        orig_factory = self.conn.row_factory
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM config WHERE id = 1")
        row = cursor.fetchone()
        self.conn.row_factory = orig_factory
        return dict(row) if row else None

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

    def add_notification(self, cupon, tienda, url, descuento=None, confianza=0.5, contexto=None, id_correo=None, asunto=None, fecha=None, fecha_expiracion=None):
        cursor = self.conn.cursor()
        if fecha:
            cursor.execute(
                "INSERT INTO notifications (cupon, tienda, URL, descuento, confianza, contexto, id_correo, asunto, Fecha, fecha_expiracion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cupon, tienda, url, descuento, confianza, contexto, id_correo, asunto, fecha, fecha_expiracion)
            )
        else:
            cursor.execute(
                "INSERT INTO notifications (cupon, tienda, URL, descuento, confianza, contexto, id_correo, asunto, fecha_expiracion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cupon, tienda, url, descuento, confianza, contexto, id_correo, asunto, fecha_expiracion)
            )
        self.conn.commit()
        logger.info(f"Cupón agregado: {cupon} - {tienda} (confianza: {confianza:.2f})")
        return cursor.lastrowid

    def add_notifications_bulk(self, rows):
        if not rows:
            return 0
        cursor = self.conn.cursor()
        cursor.executemany(
            "INSERT INTO notifications (cupon, tienda, URL, descuento, confianza, contexto, id_correo, asunto, Fecha, fecha_expiracion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        orig_factory = self.conn.row_factory
        self.conn.row_factory = sqlite3.Row
        
        query = "SELECT * FROM notifications" # Simplificamos para obtener todo y acceder por nombre
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
            where_clauses.append("(LOWER(tienda) LIKE ? OR LOWER(cupon) LIKE ?)")
            search_term = f"%{tienda.lower()}%"
            params.extend([search_term, search_term])

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
        rows = cursor.fetchall()
        
        # Restaurar factory y convertir a dicts
        self.conn.row_factory = orig_factory
        return [dict(r) for r in rows]

    def get_notification_by_id(self, cupon_id):
        cursor = self.conn.cursor()
        # Usamos row_factory temporalmente para obtener un diccionario
        orig_factory = self.conn.row_factory
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM notifications WHERE id = ?", (cupon_id,))
        row = cursor.fetchone()
        self.conn.row_factory = orig_factory
        return dict(row) if row else None

    def delete_notification(self, cupon_id):
        self.conn.execute("DELETE FROM notifications WHERE id = ?", (cupon_id,))
        self.conn.commit()

    def delete_old_notifications(self, cutoff):
        self.conn.execute("DELETE FROM notifications WHERE Fecha < ?", (cutoff,))
        self.conn.commit()

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

    def update_cupon_validity(self, cupon_id, es_valido, confianza=1.0, estado=None):
        cursor = self.conn.cursor()
        if estado:
            cursor.execute(
                "UPDATE notifications SET es_valido = ?, usuario_valido = 1, confianza = ?, estado = ? WHERE id = ?",
                (1 if es_valido else 0, confianza, estado, cupon_id)
            )
        else:
            cursor.execute(
                "UPDATE notifications SET es_valido = ?, usuario_valido = 1, confianza = ? WHERE id = ?",
                (1 if es_valido else 0, confianza, cupon_id)
            )
        self.conn.commit()
        logger.info(f"Cupón {cupon_id} actualizado (es_valido={es_valido}, estado={estado})")

    def update_cupon_full(self, cupon_id, cupon, tienda, descuento, es_valido=1, confianza=1.0):
        logger.info(f"Intentando actualizar cupón ID: {cupon_id} a usuario_valido=1")
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE notifications 
            SET cupon = ?, tienda = ?, descuento = ?, es_valido = ?, usuario_valido = 1, confianza = ?
            WHERE id = ?
        ''', (cupon, tienda, descuento, es_valido, confianza, cupon_id))
        self.conn.commit()
        rows_affected = cursor.rowcount
        logger.info(f"Cupón {cupon_id} actualizado. Filas afectadas: {rows_affected}")
        return rows_affected > 0

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

        return cursor.fetchone() is not None

    def is_positive_coupon(self, code, tienda=None):
        if not code:
            return False
        cursor = self.conn.cursor()
        if tienda:
            cursor.execute("SELECT 1 FROM positive_coupons WHERE code = ? AND (tienda = ? OR tienda IS NULL) LIMIT 1", (code.upper(), tienda))
        else:
            cursor.execute("SELECT 1 FROM positive_coupons WHERE code = ? LIMIT 1", (code.upper(),))
        return cursor.fetchone() is not None

    def get_store_stats(self, limit=5):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT tienda, COUNT(*) as total 
            FROM notifications 
            GROUP BY tienda 
            ORDER BY total DESC 
            LIMIT ?
        ''', (limit,))
        return cursor.fetchall()

    def mark_expired_coupons(self):
        """Marca automáticamente como inválidos los cupones que ya vencieron."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE notifications 
            SET es_valido = 0, estado = 'expirado', usuario_valido = 1 
            WHERE fecha_expiracion IS NOT NULL 
            AND fecha_expiracion < CURRENT_TIMESTAMP 
            AND usuario_valido = 0
        ''')
        count = cursor.rowcount
        self.conn.commit()
        if count > 0:
            logger.info(f"Auto-limpieza: {count} cupones marcados como expirados.")
        return count

    def get_brain_data(self):
        """Recopila todos los datos de aprendizaje y listas negras para exportar."""
        cursor = self.conn.cursor()
        
        data = {
            "false_positive_terms": self.get_false_positive_terms(limit=5000),
            "positive_coupons": self.get_positive_coupons(limit=5000),
            "learning_patterns": [],
            "keyword_weights": []
        }
        
        cursor.execute("SELECT tienda, patron, total_apariciones, exitosos, confianza FROM learning_patterns")
        data["learning_patterns"] = [
            {"tienda": r[0], "patron": r[1], "total": r[2], "exitosos": r[3], "confianza": r[4]} 
            for r in cursor.fetchall()
        ]
        
        cursor.execute("SELECT tienda, keyword, total_apariciones, exitosos, peso FROM keyword_weights")
        data["keyword_weights"] = [
            {"tienda": r[0], "keyword": r[1], "total": r[2], "exitosos": r[3], "peso": r[4]} 
            for r in cursor.fetchall()
        ]
        
        return data

    def import_brain_data(self, data):
        """Importa datos de cerebro de forma incremental."""
        cursor = self.conn.cursor()
        count = 0
        
        # 1. Falsos Positivos
        for term in data.get("false_positive_terms", []):
            cursor.execute("INSERT OR IGNORE INTO false_positive_terms (term) VALUES (?)", (term.upper(),))
        
        # 2. Cupones Positivos
        for cp in data.get("positive_coupons", []):
            # cp suele ser [code, tienda]
            cursor.execute("INSERT OR IGNORE INTO positive_coupons (code, tienda) VALUES (?, ?)", (cp[0].upper(), cp[1]))
            
        # 3. Patrones de Aprendizaje
        for p in data.get("learning_patterns", []):
            cursor.execute('''
                INSERT OR IGNORE INTO learning_patterns (tienda, patron, total_apariciones, exitosos, confianza)
                VALUES (?, ?, ?, ?, ?)
            ''', (p['tienda'], p['patron'], p['total'], p['exitosos'], p['confianza']))
            
        # 4. Pesos de Keywords
        for k in data.get("keyword_weights", []):
            cursor.execute('''
                INSERT OR IGNORE INTO keyword_weights (tienda, keyword, total_apariciones, exitosos, peso)
                VALUES (?, ?, ?, ?, ?)
            ''', (k['tienda'], k['keyword'], k['total'], k['exitosos'], k['peso']))
            
        self.conn.commit()
        return True

    def close(self):
        self.conn.close()
        logger.info("Conexión a base de datos cerrada")
