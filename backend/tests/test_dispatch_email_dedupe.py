"""Los reintentos de curación no repiten los correos ya enviados."""
import asyncio
import uuid

from sqlmodel import select

from models import ActionItem, MeetingSession, Project, Tenant
from routers import sessions_upload
from services import cron_service
from services.email_service import EmailService


def setup_session(db):
    tenant = Tenant(slug='dedupe-' + uuid.uuid4().hex[:8], name='Dedupe')
    db.add(tenant); db.commit(); db.refresh(tenant)
    project = Project(tenant_id=tenant.id, name='Project ' + tenant.slug)
    db.add(project); db.commit(); db.refresh(project)
    meeting = MeetingSession(tenant_id=tenant.id, project_id=project.id,
                             fireflies_id='BOT-' + tenant.slug, title='Meeting', date='2026-10-10',
                             status='pending', raw_transcript='Texto', raw_summary='Resumen')
    db.add(meeting); db.commit(); db.refresh(meeting)
    for email in ['a@example.test', 'b@example.test']:
        db.add(ActionItem(tenant_id=tenant.id, session_id=meeting.id, owner_name=email,
                          owner_email=email, title='Tarea'))
    db.commit()
    return tenant, meeting


def test_partial_email_failure_retries_only_unsent_recipient(db_session, monkeypatch):
    tenant, meeting = setup_session(db_session)
    calls = []
    fail = [True]

    async def send(self, **kwargs):
        calls.append(kwargs['to_email'])
        if kwargs['to_email'].startswith('b@') and fail[0]:
            raise RuntimeError('Temporary failure')
        return True

    async def sleep(_):
        pass

    monkeypatch.setattr(EmailService, 'send_action_items_batch_email', send)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    ids = [t.id for t in db_session.exec(select(ActionItem).where(ActionItem.session_id == meeting.id))]
    request = sessions_upload.DispatchEmailsRequest(action_item_ids=ids, only_unsent=True,
                                                    custom_pdf_b64='dGVzdA==')
    first = asyncio.run(sessions_upload.dispatch_emails(meeting.id, request, db_session, tenant))
    assert [r['status'] for r in first['results']] == ['success', 'failed']
    fail[0] = False
    second = asyncio.run(sessions_upload.dispatch_emails(meeting.id, request, db_session, tenant))
    assert {r['status'] for r in second['results']} == {'skipped', 'success'}
    assert calls == ['a@example.test', 'b@example.test', 'b@example.test']
    item = db_session.get(ActionItem, ids[0]); item.owner_email = 'c@example.test'
    db_session.add(item); db_session.commit()
    asyncio.run(sessions_upload.dispatch_emails(meeting.id, request, db_session, tenant))
    assert calls[-1] == 'c@example.test'


def test_no_sender_does_not_mark_email_as_sent(db_session, monkeypatch):
    tenant, meeting = setup_session(db_session)

    async def send(self, **kwargs):
        return False

    monkeypatch.setattr(EmailService, 'send_action_items_batch_email', send)
    item = db_session.exec(select(ActionItem).where(ActionItem.session_id == meeting.id)).first()
    result = asyncio.run(sessions_upload.dispatch_emails(meeting.id,
        sessions_upload.DispatchEmailsRequest(action_item_ids=[item.id], only_unsent=True,
                                              custom_pdf_b64='dGVzdA=='), db_session, tenant))
    db_session.refresh(item)
    assert result['results'][0]['status'] == 'failed'
    assert item.email_sent_at == ''


def test_auto_curation_without_external_routes_completes(db_session, test_engine, monkeypatch):
    _, meeting = setup_session(db_session)
    monkeypatch.setattr(cron_service, 'engine', test_engine)
    calls = []

    async def emails(sid, request, *args):
        calls.append(request.only_unsent)
        return {'results': [{'id': i, 'status': 'success'} for i in request.action_item_ids]}

    async def platforms(*args):
        raise AssertionError('No hay rutas que despachar')

    async def notice(*args):
        pass

    monkeypatch.setattr(sessions_upload, 'dispatch_emails', emails)
    monkeypatch.setattr(sessions_upload, 'dispatch_platforms', platforms)
    monkeypatch.setattr(cron_service, '_send_auto_dispatch_done_notice', notice)
    asyncio.run(cron_service._auto_dispatch_session(meeting.id))
    db_session.refresh(meeting)
    assert meeting.status == 'processed'
    assert calls == [True]


def test_partial_failure_does_not_mark_session_processed(db_session, test_engine, monkeypatch):
    _, meeting = setup_session(db_session)
    monkeypatch.setattr(cron_service, 'engine', test_engine)

    async def emails(*args):
        return {'results': [{'status': 'success'}, {'status': 'failed'}]}

    monkeypatch.setattr(sessions_upload, 'dispatch_emails', emails)
    asyncio.run(cron_service._auto_dispatch_session(meeting.id))
    db_session.refresh(meeting)
    assert meeting.status == 'pending'


def test_platform_retry_does_not_resend_successful_emails(db_session, test_engine, monkeypatch):
    tenant, meeting = setup_session(db_session)
    monkeypatch.setattr(cron_service, 'engine', test_engine)
    from routers import fireflies
    monkeypatch.setattr(fireflies, '_routings_for_project_dispatch', lambda *args: [object()])
    actual_dispatch = sessions_upload.dispatch_emails
    sends = []
    platform_calls = []

    async def send(self, **kwargs):
        sends.append(kwargs['to_email'])
        return True

    async def emails(sid, request, db, tenant):
        request.custom_pdf_b64 = 'dGVzdA=='
        return await actual_dispatch(sid, request, db, tenant)

    async def platforms(*args):
        platform_calls.append(1)
        status = 'failed' if len(platform_calls) == 1 else 'success'
        return {'results': [{'status': status}]}

    async def notice(*args):
        pass

    async def sleep(_):
        pass

    monkeypatch.setattr(EmailService, 'send_action_items_batch_email', send)
    monkeypatch.setattr(sessions_upload, 'dispatch_emails', emails)
    monkeypatch.setattr(sessions_upload, 'dispatch_platforms', platforms)
    monkeypatch.setattr(cron_service, '_send_auto_dispatch_done_notice', notice)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    asyncio.run(cron_service._auto_dispatch_session(meeting.id))
    db_session.refresh(meeting)
    assert meeting.status == 'pending'
    asyncio.run(cron_service._auto_dispatch_session(meeting.id))
    db_session.refresh(meeting)
    assert meeting.status == 'processed'
    assert sends == ['a@example.test', 'b@example.test']


def test_missing_email_does_not_block_other_session_recipients(db_session, test_engine, monkeypatch):
    tenant, meeting = setup_session(db_session)
    item = db_session.exec(select(ActionItem).where(ActionItem.session_id == meeting.id)).first()
    item.owner_email = ''; db_session.add(item); db_session.commit()
    monkeypatch.setattr(cron_service, 'engine', test_engine)
    emailed = []
    notices = []

    async def notice(*args):
        notices.append(args[0])

    async def emails(sid, request, db, tenant):
        emailed.extend(request.action_item_ids)
        return {'results': [{'status': 'success'} for _ in request.action_item_ids]}

    monkeypatch.setattr(cron_service, '_send_auto_dispatch_blocked_notice', notice)
    monkeypatch.setattr(cron_service, '_send_auto_dispatch_done_notice', notice)
    monkeypatch.setattr(sessions_upload, 'dispatch_emails', emails)
    asyncio.run(cron_service._auto_dispatch_session(meeting.id))
    db_session.refresh(meeting)
    assert meeting.status == 'processed'
    assert item.id not in emailed
    assert len(emailed) == 1


def test_missing_email_notice_only_to_tenant_owner_once(db_session, test_engine, monkeypatch):
    from models import User, Role, Notification
    tenant, meeting = setup_session(db_session)
    role = db_session.exec(select(Role).where(Role.name == 'admin')).first()
    if not role:
        role = Role(name='admin'); db_session.add(role); db_session.commit(); db_session.refresh(role)
    users = []
    for name in ['owner', 'other']:
        user = User(tenant_id=tenant.id, email=name + '@' + tenant.slug + '.test',
                    full_name=name, hashed_password='test', role_id=role.id)
        db_session.add(user); db_session.commit(); db_session.refresh(user); users.append(user)
    tenant.owner_user_id = users[0].id; db_session.add(tenant); db_session.commit()
    sent = []
    async def send(self, **kwargs):
        sent.append(kwargs['to_email']); return True
    monkeypatch.setattr(EmailService, 'send_auto_dispatch_blocked_email', send)
    monkeypatch.setattr(cron_service, 'engine', test_engine)
    for _ in range(2):
        asyncio.run(cron_service._send_auto_dispatch_blocked_notice(meeting.id, tenant.id, 1, 1, 60))
    assert sent == [users[0].email]
    notices = db_session.exec(select(Notification).where(Notification.entity_id == meeting.id,
                              Notification.tenant_id == tenant.id)).all()
    assert [n.user_id for n in notices] == [users[0].id]
