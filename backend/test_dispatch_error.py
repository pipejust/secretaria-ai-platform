import asyncio
from database import engine
from sqlmodel import Session
from routers.sessions_upload import dispatch_emails, DispatchEmailsRequest

async def test():
    with Session(engine) as db:
        req = DispatchEmailsRequest(action_item_ids=[1])
        try:
            res = await dispatch_emails(8, req, db)
            print("SUCCESS:", res)
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
