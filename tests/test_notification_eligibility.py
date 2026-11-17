"""Notification policy has no network/DB dependencies."""
from datetime import datetime,date,timezone,timedelta
import unittest
from unittest.mock import patch
from app.services.notification_eligibility import configured_policy,eligibility_reason

class EligibilityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,5,12,tzinfo=timezone.utc)
        self.policy=(self.now-timedelta(hours=1),7)
        self.item=dict(intent='live_new',corrected_url=False,first_seen=self.now,official_date=date(2026,11,5),date_provenance='official_source')
    def test_new_dated_live_document_can_be_eligible(self):
        self.assertIsNone(eligibility_reason(self.item,self.policy,self.now))
    def test_no_policy_is_fail_closed(self):
        with patch.dict('os.environ',{},clear=True):self.assertIsNone(configured_policy())
        self.assertEqual(eligibility_reason(self.item,None,self.now),'policy_not_configured')
    def test_historical_unknown_and_reprocess_never_qualify(self):
        for intent in ('unknown','historical_import','reprocess'):
            self.assertEqual(eligibility_reason(dict(self.item,intent=intent),self.policy,self.now),'non_live_intent')
    def test_unknown_date_corrections_and_stale_publication_are_held(self):
        for fields in ({'official_date':None},{'corrected_url':True},{'official_date':date(2025,1,1)},{'first_seen':self.now-timedelta(days=1)}):
            self.assertIsNotNone(eligibility_reason(dict(self.item,**fields),self.policy,self.now))
    def test_cutover_requires_timezone_and_bounded_window(self):
        for raw,days in [('2026-11-05','7'),('2026-11-05T00:00:00Z','100'),('invalid','7')]:
            with patch.dict('os.environ',{'NOTIFICATION_CUTOVER_UTC':raw,'NOTIFICATION_MAX_AGE_DAYS':days}):self.assertIsNone(configured_policy())


class PublisherEligibilityTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        permit={'token':'11111111-1111-1111-1111-111111111111','snapshot':{
            'content':{'rendered_text':'synthetic','title':'synthetic','body':'synthetic','url':'/'},'delivery_context':{'chat_id':'@synthetic'}}}
        permit['attempt_id']=1
        self.permit=permit
        p=patch('run_send_notifications.prepare_push_recipients',return_value=([{'id':1,'endpoint':'https://example.invalid','p256dh_key':'synthetic','auth_key':'synthetic'}],1));p.start();self.addCleanup(p.stop)

    def test_legacy_rows_cannot_bypass_the_delivery_claim(self):
        from contextlib import redirect_stdout
        import io
        import run_send_notifications as push
        import run_publish_notifications as telegram
        for module,listing,transport in [(push,'list_pending_push_notification_events','dispatch_transport'),
                                         (telegram,'list_pending_notification_posts','dispatch_transport')]:
            event={'id':1,'event_key':'legacy','event_type':'synthetic','rendered_text':'old','title':'old','body':'old','url':'/'}
            configured='is_push_configured' if module is push else 'is_telegram_configured'
            with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'1'}),patch.object(module,'get_connection',return_value=unittest.mock.Mock()),patch.object(module,configured,return_value=True),patch.object(module,listing,return_value=[event]),patch.object(module,'claim_notification_delivery',return_value=None),patch.object(module,transport) as send,redirect_stdout(io.StringIO()):
                module.main()
            send.assert_not_called()

    def test_transport_failure_is_retained_as_uncertain(self):
        from contextlib import redirect_stdout
        import io
        import run_publish_notifications as module
        event={'id':1,'event_key':'eligible','event_type':'synthetic','rendered_text':'synthetic'}
        with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'1'}),patch.object(module,'get_connection',return_value=unittest.mock.Mock()),patch.object(module,'is_telegram_configured',return_value=True),patch.object(module,'list_pending_notification_posts',return_value=[event]),patch.object(module,'claim_notification_delivery',return_value=self.permit),patch.object(module, 'dispatch_transport',side_effect=RuntimeError('synthetic')),patch.object(module,'finish_delivery_attempt') as finish,patch.object(module,'mark_notification_post_failed'),patch.object(module,'mark_notification_post_published') as sent,redirect_stdout(io.StringIO()):
            module.main()
        sent.assert_not_called()
        self.assertEqual(finish.call_args.args[2],'uncertain')

    def test_partial_push_delivery_is_not_marked_sent(self):
        from contextlib import redirect_stdout
        import io
        import run_send_notifications as module
        event={'id':1,'event_key':'eligible','title':'synthetic','body':'synthetic','url':'/'}
        with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'1'}),patch.object(module,'get_connection',return_value=unittest.mock.Mock()),patch.object(module,'is_push_configured',return_value=True),patch.object(module,'list_pending_push_notification_events',return_value=[event]),patch.object(module,'claim_notification_delivery',return_value=self.permit),patch.object(module, 'dispatch_transport',side_effect=[{'ok':True},{'ok':False}]),patch.object(module,'prepare_push_recipients',return_value=([dict(id=i,endpoint='https://example.invalid',p256dh_key='synthetic',auth_key='synthetic') for i in (1,2)],2)),patch.object(module,'finish_delivery_attempt') as finish,patch.object(module,'mark_push_notification_event_failed') as failed,patch.object(module,'mark_push_notification_event_sent') as sent,redirect_stdout(io.StringIO()):
            module.main()
        sent.assert_not_called()
        self.assertEqual(finish.call_args.args[2],'uncertain')
        self.assertEqual(failed.call_args.kwargs['sent_count'],1)
        self.assertEqual(failed.call_args.kwargs['failed_count'],1)

    def test_logging_failure_cannot_downgrade_confirmed_telegram(self):
        import run_publish_notifications as module
        event={'id':1,'event_key':'eligible','event_type':'synthetic','rendered_text':'synthetic'}
        def log(*args,**kw):
            if args and 'published telegram' in str(args[0]):raise BrokenPipeError('synthetic_logging_failure')
        with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'1'}),patch.object(module,'get_connection',return_value=unittest.mock.Mock()),patch.object(module,'is_telegram_configured',return_value=True),patch.object(module,'list_pending_notification_posts',return_value=[event]),patch.object(module,'claim_notification_delivery',return_value=self.permit),patch.object(module, 'dispatch_transport',return_value={'ok':True,'result':{'message_id':1}}),patch.object(module,'finish_delivery_attempt') as finish,patch.object(module,'mark_notification_post_failed') as failed,patch.object(module,'mark_notification_post_published') as published,patch('builtins.print',side_effect=log):
            module.main()
        published.assert_called_once()
        failed.assert_not_called()
        self.assertEqual(finish.call_count,1)
        self.assertEqual(finish.call_args.args[2],'sent')

    def test_new_notice_copy_translates_without_touching_date(self):
        from app.web.i18n import translate_text
        text='Documento de la publicación oficial de 2026-11-05 procesado y validado. Consulta su resultado y la fuente oficial.'
        result=translate_text(text,'va')
        self.assertIn('Document de la publicació oficial de 2026-11-05',result)
        self.assertIn('processat i validat',result)
