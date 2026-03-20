import sys
sys.path.append('.')
from sqlmodel import Session, select
from database import engine
from models import MeetingSession, ActionItem
from routers.sessions_upload import generate_word_document_bytes, __build_corporate_data
from services.pdf_generator import CorporatePDFGenerator

def test():
    with Session(engine) as db:
        # Get first session that has some data
        session_obj = db.exec(select(MeetingSession)).first()
        if not session_obj:
            print("No sessions found in DB")
            return
            
        action_items = db.exec(select(ActionItem).where(ActionItem.session_id == session_obj.id)).all()
        
        print(f"Testing export for session {session_obj.id} - {session_obj.title}")
        
        # Test DOCX
        print("Generating DOCX...")
        docx_buffer = generate_word_document_bytes(session_obj, action_items, db)
        with open("test_export.docx", "wb") as f:
            f.write(docx_buffer.getvalue())
        print("DOCX saved to test_export.docx")
        
        # Test PDF
        print("Generating PDF...")
        data = __build_corporate_data(session_obj, action_items)
        pdf_gen = CorporatePDFGenerator(data)
        pdf_buffer = pdf_gen.generar_buffer()
        with open("test_export.pdf", "wb") as f:
            f.write(pdf_buffer.getvalue())
        print("PDF saved to test_export.pdf")

if __name__ == "__main__":
    test()
