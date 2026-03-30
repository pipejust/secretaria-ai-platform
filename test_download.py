import urllib.request
import traceback

url = "https://oqrzuuwwmsbxhlmkwzps.supabase.co/storage/v1/object/public/templates/project_1/Colp%20planilla.docx"
local_path = "/tmp/test_colp.docx"

try:
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    with urllib.request.urlopen(req) as response, open(local_path, 'wb') as out_file:
        out_file.write(response.read())
    print("Download success!")
except Exception as e:
    print(f"Download failed: {e}")
    traceback.print_exc()

