import os
from supabase import create_client, Client
from config import settings

def get_supabase_client() -> Client:
    url = settings.supabase_url or os.environ.get("SUPABASE_URL")
    key = settings.supabase_key or os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        raise ValueError("Supabase URL and Key must be provided in environment variables")
        
    return create_client(url, key)

def upload_file_to_bucket(bucket_name: str, file_path: str, destination_path: str) -> str:
    """Subida de un archivo físico al bucket de Supabase."""
    supabase = get_supabase_client()
    
    # Asegurar que el bucket existe o falla amigablemente
    with open(file_path, 'rb') as f:
        response = supabase.storage.from_(bucket_name).upload(
            file=f,
            path=destination_path,
            file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        )
        
    # Obtener URL pública
    public_url = supabase.storage.from_(bucket_name).get_public_url(destination_path)
    return public_url
