import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import db

# Los permisos que queremos pedirle al usuario: Gmail (solo lectura y borradores) y Calendar (lectura y escritura)
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/calendar'
]

def authenticate_google(user_id: int):
    """Abre el navegador para que el usuario inicie sesión y guarda el token en Supabase."""
    print("Iniciando flujo de autenticación de Google...")
    creds = None
    
    # Intentar obtener el token de la base de datos primero
    token_json = db.get_google_token(user_id)
    if token_json:
        print("Encontramos un token guardado en Supabase.")
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        
    # Si no hay token válido, pedirle al usuario que inicie sesión
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            print("Refrescando token expirado...")
            creds.refresh(Request())
        else:
            print("No hay token válido. Abriendo navegador web para iniciar sesión...")
            # Aquí usamos el archivo credentials.json que descargaste
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Guardar el nuevo token en Supabase para este usuario
        db.save_google_token(user_id, creds.to_json())
        print(f"✅ ¡Token de Google guardado exitosamente en la base de datos para el usuario {user_id}!")
    
    return creds

if __name__ == '__main__':
    # Aquí simulamos el ID de usuario de Telegram del Jefe (lo sacaremos de db o env)
    # Reemplaza 'tu_id_aqui' con tu ID real de Telegram, o lo obtenemos temporalmente.
    user_id = os.environ.get("ADMIN_USER_ID")
    if not user_id:
        user_id = input("Introduce tu ID de Telegram (solo números): ")
    
    authenticate_google(int(user_id))
