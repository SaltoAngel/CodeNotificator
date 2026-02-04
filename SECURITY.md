# 🔒 Guía de Seguridad - CodeNotificator

## ⚠️ Archivos Sensibles - NO SUBIR A GITHUB

Los siguientes archivos contienen información sensible y **NUNCA** deben subirse a GitHub:

### 🔑 Credenciales y Tokens

- `token.json` - Tokens de autenticación de Gmail
- `credentials.json` - Credenciales OAuth de Google Cloud
- `.env` - Variables de entorno (si las usas)

### 💾 Datos Personales

- `notifications.db` - Base de datos con tus cupones y correos
- `logs/*.log` - Archivos de log que pueden contener información sensible
- `assets/logos/*` - Logos descargados de tiendas

## ✅ Configuración Inicial

### 1. Obtener Credenciales de Google Cloud

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Crea un nuevo proyecto o selecciona uno existente
3. Habilita la **Gmail API**:
   - Ve a "APIs y servicios" → "Biblioteca"
   - Busca "Gmail API" y habilítala
4. Crea credenciales OAuth 2.0:
   - Ve a "APIs y servicios" → "Credenciales"
   - Clic en "Crear credenciales" → "ID de cliente de OAuth"
   - Tipo de aplicación: "Aplicación de escritorio"
   - Descarga el archivo JSON
5. Renombra el archivo descargado a `credentials.json`
6. Colócalo en la raíz del proyecto

### 2. Primera Ejecución

Al ejecutar la aplicación por primera vez:

1. Se abrirá tu navegador para autorizar el acceso a Gmail
2. Inicia sesión con tu cuenta de Google
3. Acepta los permisos solicitados
4. Se generará automáticamente el archivo `token.json`

## 🛡️ Verificación de Seguridad

Antes de hacer `git push`, verifica que no estés subiendo archivos sensibles:

```bash
# Ver qué archivos se van a subir
git status

# Verificar que los archivos sensibles NO aparezcan
git ls-files | grep -E "(token\.json|credentials\.json|\.db$)"
```

Si algún archivo sensible aparece, elimínalo del índice:

```bash
git rm --cached nombre_del_archivo
```

## 📋 Checklist Antes de Subir a GitHub

- [ ] El archivo `.gitignore` está actualizado
- [ ] `token.json` NO está en el repositorio
- [ ] `credentials.json` NO está en el repositorio
- [ ] `notifications.db` NO está en el repositorio
- [ ] Los archivos de log NO están en el repositorio
- [ ] Has creado `credentials.json.example` para guiar a otros usuarios

## 🔄 Si Ya Subiste Archivos Sensibles

Si accidentalmente ya subiste archivos sensibles a GitHub:

### Opción 1: Eliminar del último commit (si no has hecho push)

```bash
git rm --cached archivo_sensible
git commit --amend
```

### Opción 2: Si ya hiciste push

1. **Revoca inmediatamente las credenciales** en Google Cloud Console
2. Elimina el archivo del historial:
   ```bash
   git filter-branch --force --index-filter \
     "git rm --cached --ignore-unmatch archivo_sensible" \
     --prune-empty --tag-name-filter cat -- --all
   ```
3. Fuerza el push:
   ```bash
   git push origin --force --all
   ```
4. **Genera nuevas credenciales** en Google Cloud Console

### Opción 3: Usar BFG Repo-Cleaner (recomendado para repositorios grandes)

```bash
# Instalar BFG
# Debian/Ubuntu: sudo apt install bfg
# Arch: yay -S bfg

# Limpiar archivos sensibles
bfg --delete-files token.json
bfg --delete-files credentials.json
bfg --delete-files notifications.db

# Limpiar y forzar push
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push origin --force --all
```

## 🔐 Mejores Prácticas

1. **Nunca** hagas commit de archivos `.json` que contengan credenciales
2. **Siempre** verifica el `.gitignore` antes del primer commit
3. **Usa** archivos `.example` para mostrar la estructura sin exponer datos reales
4. **Revoca** las credenciales inmediatamente si las expones accidentalmente
5. **Considera** usar variables de entorno para configuraciones sensibles

## 📚 Recursos Adicionales

- [GitHub: Removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- [Google Cloud: OAuth 2.0](https://developers.google.com/identity/protocols/oauth2)
- [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)

---

**⚠️ IMPORTANTE**: Si expones credenciales accidentalmente, considera que están comprometidas y deben ser revocadas inmediatamente, incluso si las eliminas del repositorio.
