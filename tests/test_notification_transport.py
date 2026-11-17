import os,time,unittest,sys
from types import SimpleNamespace
from unittest.mock import patch
from app.services import notification_transport as transport


class BoundedTransportTests(unittest.TestCase):
    def test_expired_provider_response_is_definitive_not_fake_ack(self):
        class Expired(Exception):response=SimpleNamespace(status_code=410)
        from unittest.mock import Mock
        fake=SimpleNamespace(WebPushException=Expired,webpush=Mock(side_effect=Expired()))
        with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1'}),patch.dict(sys.modules,{'pywebpush':fake}):
            result=transport.worker(dict(kind='push',subscription=dict(endpoint='https://example.invalid',p256dh_key='synthetic',auth_key='synthetic'),title='synthetic',body='synthetic',url='/'))
        self.assertFalse(result['ok']);self.assertTrue(result['expired'])

    def test_spawned_worker_has_no_database_environment_or_sockets(self):
        with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1','RADAR_DOCENT_DB_URL':'must_not_inherit','PGSERVICE':'must_not_inherit'}),patch.object(transport,'WORKER_MODULE','tests.notification_transport_stub'):
            result=transport.dispatch_transport({'scenario':'ok'},timeout=1)
        self.assertTrue(result['ok']);self.assertFalse(result['inherited_socket']);self.assertFalse(result['inherited_database_env'])

    def test_never_responding_provider_is_killed_by_hard_deadline(self):
        start=time.monotonic()
        with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1'}),patch.object(transport,'WORKER_MODULE','tests.notification_transport_stub'):
            result=transport.dispatch_transport({'scenario':'never'},timeout=.15)
        self.assertTrue(result['uncertain']);self.assertEqual(result['reason'],'transport_deadline')
        self.assertLess(time.monotonic()-start,2)

    def test_disabled_never_launches_worker(self):
        with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'0'}),patch.object(transport.subprocess,'Popen') as spawn:
            result=transport.dispatch_transport({})
        spawn.assert_not_called();self.assertTrue(result['not_started'])

    def test_budget_and_recipient_limit_do_not_fake_delivery(self):
        recipients=[dict(id=i,endpoint='https://example.invalid',p256dh_key='synthetic',auth_key='synthetic') for i in range(100)]
        result=transport.send_push_snapshot(recipients,105,dict(title='x',body='x',url='/'),time.monotonic()+10,dispatch=lambda *a,**k:{'ok':True})
        self.assertEqual(result['sent'],100);self.assertEqual(result['not_started'],5)
        result=transport.send_push_snapshot(recipients,105,dict(title='x',body='x',url='/'),time.monotonic()-1)
        self.assertEqual(result['sent'],0);self.assertEqual(result['not_started'],105)

    def test_one_active_request_per_process(self):
        transport._slot.acquire()
        try:
            with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1'}),patch.object(transport.subprocess,'Popen') as spawn:
                result=transport.dispatch_transport({})
            spawn.assert_not_called();self.assertTrue(result['not_started'])
        finally:transport._slot.release()
