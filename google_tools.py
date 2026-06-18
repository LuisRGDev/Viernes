import json
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import db

# Scopes (mismos que en la autenticación)
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/calendar'
]

def get_google_credentials(user_id):
    """Obtiene y construye el objeto de credenciales desde Supabase."""
    token_json = db.get_google_token(user_id)
    if not token_json:
        return None
    return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)

def read_gmail(max_results: int = 5) -> str:
    """Lee los correos más recientes en la bandeja de entrada (Inbox).
    Usa esta herramienta cuando el usuario pregunte por sus correos o si le ha llegado algo nuevo.
    """
    from bot import current_user_id
    user_id = current_user_id.get()
    creds = get_google_credentials(user_id)
    if not creds:
        return "No tienes vinculada tu cuenta de Google. Ejecuta el script de autenticación primero."

    try:
        service = build('gmail', 'v1', credentials=creds)
        # Buscar correos en INBOX
        results = service.users().messages().list(userId='me', labelIds=['INBOX'], maxResults=max_results).execute()
        messages = results.get('messages', [])

        if not messages:
            return "No tienes correos recientes en tu bandeja de entrada."

        salida = "Tus correos recientes:\n\n"
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['Subject', 'From', 'Date']).execute()
            headers = msg_data.get('payload', {}).get('headers', [])
            
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '(Sin Asunto)')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), '(Desconocido)')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), '(Sin Fecha)')
            snippet = msg_data.get('snippet', '')
            
            salida += f"De: {sender}\nAsunto: {subject}\nFecha: {date}\nResumen: {snippet}\n---\n"
        
        return salida
    except Exception as e:
        return f"Error leyendo Gmail: {e}"

def draft_email(to: str, subject: str, body: str) -> str:
    """Crea un borrador de correo electrónico en Gmail para que el usuario lo envíe después.
    Usa esta herramienta cuando el usuario te pida redactar, contestar o enviar un correo.
    """
    import base64
    from email.message import EmailMessage
    from bot import current_user_id

    user_id = current_user_id.get()
    creds = get_google_credentials(user_id)
    if not creds:
        return "No tienes vinculada tu cuenta de Google."

    try:
        service = build('gmail', 'v1', credentials=creds)
        
        message = EmailMessage()
        message.set_content(body)
        message['To'] = to
        message['Subject'] = subject

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_draft_request_body = {'message': {'raw': encoded_message}}
        
        draft = service.users().drafts().create(userId='me', body=create_draft_request_body).execute()
        return f"Borrador creado exitosamente. Puedes revisarlo y enviarlo desde tu aplicación de Gmail. (ID del borrador: {draft['id']})"
    except Exception as e:
        return f"Error creando borrador: {e}"

def list_events(days: int = 1) -> str:
    """Obtiene los próximos eventos y reuniones de Google Calendar.
    Usa esta herramienta cuando el usuario pregunte por su agenda, reuniones o eventos programados.
    """
    from bot import current_user_id
    user_id = current_user_id.get()
    creds = get_google_credentials(user_id)
    if not creds:
        return "No tienes vinculada tu cuenta de Google."

    try:
        service = build('calendar', 'v3', credentials=creds)
        
        tz = timezone(timedelta(hours=-6))
        now = datetime.now(tz)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days)).isoformat()
        
        events_result = service.events().list(
            calendarId='primary', timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

        if not events:
            return f"No tienes eventos próximos en los siguientes {days} días."

        salida = f"Tus eventos en los próximos {days} días:\n\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            summary = event.get('summary', '(Sin Título)')
            salida += f"- {summary} (Inicio: {start}, Fin: {end})\n"
        
        return salida
    except Exception as e:
        return f"Error obteniendo calendario: {e}"

def create_event(summary: str, start_datetime_iso: str, end_datetime_iso: str, description: str = "") -> str:
    """Crea un evento en Google Calendar.
    start_datetime_iso y end_datetime_iso deben estar en formato ISO 8601 (ej. '2026-06-18T15:00:00-06:00').
    Usa esta herramienta cuando el usuario pida agendar algo, programar una reunión o agregar un evento a su calendario.
    Asegúrate de deducir el año, mes y día actuales correctamente usando la herramienta get_current_datetime si el usuario dice 'mañana' o 'el viernes'.
    """
    from bot import current_user_id
    user_id = current_user_id.get()
    creds = get_google_credentials(user_id)
    if not creds:
        return "No tienes vinculada tu cuenta de Google."

    try:
        service = build('calendar', 'v3', credentials=creds)
        
        event = {
          'summary': summary,
          'description': description,
          'start': {
            'dateTime': start_datetime_iso,
            'timeZone': 'America/Mexico_City',
          },
          'end': {
            'dateTime': end_datetime_iso,
            'timeZone': 'America/Mexico_City',
          },
        }

        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"Evento agendado exitosamente: {event.get('htmlLink')}"
    except Exception as e:
        return f"Error creando evento: {e}"
