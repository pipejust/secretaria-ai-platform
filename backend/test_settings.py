from sqlmodel import Session, create_engine
import json
from config import settings
from models import IntegrationSetting

# Simple test script without spinning up the whole server
engine = create_engine(settings.database_url)

payload = {
    "fireflies": {"apiKey": "123", "webhookUrl": "https://test.com"},
    "smtp": {"apiKey": "abc"}
}

try:
    with Session(engine) as session:
        for provider_name, config_obj in payload.items():
            print(f"Processing {provider_name}")
            existing = session.query(IntegrationSetting).filter(IntegrationSetting.provider_name == provider_name).first()
            
            config_json_str = json.dumps(config_obj)
            
            if existing:
                print(f"Updating existing {provider_name}")
                existing.config_json = config_json_str
                session.add(existing)
            else:
                print(f"Creating new {provider_name}")
                new_setting = IntegrationSetting(
                    provider_name=provider_name,
                    config_json=config_json_str,
                    is_active=True
                )
                session.add(new_setting)
                
        session.commit()
        print("Success!")
except Exception as e:
    print(f"EXCEPTION CAUGHT: {e}")
    import traceback
    traceback.print_exc()
