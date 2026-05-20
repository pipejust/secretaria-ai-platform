from sqlmodel import Session
from database import engine
from models import MeetingSession, ActionItem
from sqlmodel import select

with Session(engine) as db:
    s = db.get(MeetingSession, 79)
    if not s:
        print("NO existe sesión 79 en LOCAL")
        # Buscar más recientes
        recent = db.exec(select(MeetingSession).order_by(MeetingSession.id.desc()).limit(5)).all()
        print("Últimas 5 sesiones locales:")
        for r in recent:
            print(f"  id={r.id} ff={(r.fireflies_id or '')[:30]} status={r.status} sum={len(r.raw_summary or '')}c err={(r.processing_error or '')[:50]!r}")
    else:
        print(f"Sesión 79 LOCAL:")
        print(f"  title={s.title!r}")
        print(f"  status={s.status}")
        print(f"  fireflies_id={s.fireflies_id}")
        print(f"  tenant_id={s.tenant_id}")
        print(f"  raw_transcript={len(s.raw_transcript or '')}c")
        print(f"  raw_summary={len(s.raw_summary or '')}c")
        print(f"  processing_error={s.processing_error or '(empty)'}")
        print(f"  processing_attempts={s.processing_attempts}")
        print(f"  processing_completed_at={s.processing_completed_at!r}")
        print(f"  session_ready_email_sent_at={getattr(s, 'session_ready_email_sent_at', '?')!r}")
