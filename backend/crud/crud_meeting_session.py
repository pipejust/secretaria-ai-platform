from crud.base import CRUDBase
from models import MeetingSession

class CRUDMeetingSession(CRUDBase[MeetingSession, MeetingSession, MeetingSession]):
    pass

meeting_session = CRUDMeetingSession(MeetingSession)
