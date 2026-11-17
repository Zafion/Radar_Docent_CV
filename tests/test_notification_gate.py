"""Regresiones del interruptor de notificaciones; emisores simulados, sin red."""
import unittest
import io
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

class NotificationGateTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        permit={'token':'11111111-1111-1111-1111-111111111111','snapshot':{
            'content':{'rendered_text':'synthetic','title':'synthetic','body':'synthetic','url':'/'},'delivery_context':{'chat_id':'@synthetic'}}}
        permit['attempt_id']=1
        self.permit=permit
        p=patch('run_send_notifications.prepare_push_recipients',return_value=([{'id':1,'endpoint':'https://example.invalid','p256dh_key':'synthetic','auth_key':'synthetic'}],1));p.start();self.addCleanup(p.stop)

    def test_push_publisher_disabled_never_opens_database(self):
        import run_send_notifications as sender
        conn=Mock()
        with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'0'}),patch.object(sender,'get_connection',return_value=conn) as get,patch.object(sender,'list_pending_push_notification_events',return_value=[]):
            sender.main()
        get.assert_not_called()

    def test_telegram_publisher_disabled_never_opens_database(self):
        import run_publish_notifications as sender
        with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'0'}),patch.object(sender,'get_connection',return_value=Mock()) as get,patch.object(sender,'list_pending_notification_posts',return_value=[]):
            sender.main()
        get.assert_not_called()

    def test_telegram_entrypoints_disabled_never_reach_transport(self):
        from app.services import telegram_notifications as telegram
        with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'0','TELEGRAM_BOT_TOKEN':'synthetic','TELEGRAM_CHANNEL_ID':'@synthetic'}),patch.object(telegram,'_telegram_json_request',return_value={'ok':True}) as send:
            telegram.send_telegram_message('test')
            telegram.send_telegram_photo('https://example.invalid/image.png')
        send.assert_not_called()

    def test_push_disabled_never_reads_recipients(self):
        from app.services import push_notifications as push
        with patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'0'}),patch.object(push,'is_push_configured',return_value=True),patch.object(push,'list_active_push_subscriptions',return_value=[]) as recipients:
            push.send_push_notification_to_all(Mock(),title='test',body='test',url='/')
        recipients.assert_not_called()

    def test_only_explicit_one_enables_notifications(self):
        from app.services.notification_policy import notifications_enabled
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(notifications_enabled())
            for value in ('0', '', 'true', 'yes', 'on', ' 1 ', '1'):
                with self.subTest(value=value), patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': value}):
                    self.assertEqual(notifications_enabled(), value == '1')

    def test_disabled_private_telegram_transport_never_opens_network(self):
        from app.services import telegram_notifications as telegram
        with patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': '0'}), patch.object(telegram.urllib.request, 'urlopen') as request:
            result = telegram._telegram_json_request('sendMessage', {})
        request.assert_not_called()
        self.assertTrue(result['disabled'])
        self.assertFalse(result['ok'])

    def test_enabled_telegram_wrappers_keep_transport_contract(self):
        from app.services import telegram_notifications as telegram
        reply = {'ok': True, 'result': {'message_id': 17}}
        with patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': '1', 'TELEGRAM_BOT_TOKEN': 'synthetic', 'TELEGRAM_CHANNEL_ID': '@synthetic'}), patch.object(telegram, '_telegram_json_request', return_value=reply) as send:
            self.assertEqual(telegram.send_telegram_message('synthetic'), reply)
            self.assertEqual(telegram.send_telegram_photo('https://example.invalid/image.png', 'synthetic'), reply)
        self.assertEqual([call.args[0] for call in send.call_args_list], ['sendMessage', 'sendPhoto'])

    def test_enabled_push_uses_simulated_transport(self):
        from app.services import push_notifications as push
        transport = Mock(return_value=SimpleNamespace(status_code=201))
        fake_module = SimpleNamespace(WebPushException=type('FakePushException', (Exception,), {}), webpush=transport)
        sub = {'endpoint': 'https://example.invalid/push', 'p256dh_key': 'synthetic', 'auth_key': 'synthetic'}
        with patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': '1'}), patch.dict(sys.modules, {'pywebpush': fake_module}), patch.object(push, 'is_push_configured', return_value=True), patch.object(push, 'VAPID_PRIVATE_KEY', 'synthetic'), patch.object(push, 'list_active_push_subscriptions', return_value=[sub]):
            from app.services.notification_transport import worker
            result = worker(dict(kind='push',subscription=sub,title='test',body='test',url='/'))
        transport.assert_called_once()
        self.assertTrue(result['ok'])
        self.assertEqual(transport.call_args.kwargs['timeout'],10)

    def test_push_publisher_does_not_mark_disabled_send_as_sent(self):
        import run_send_notifications as sender
        event = {'id': 1, 'event_key': 'synthetic', 'title': 'test', 'body': 'test', 'url': '/'}
        with patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': '1'}), patch.object(sender, 'get_connection', return_value=Mock()), patch.object(sender, 'claim_notification_delivery', return_value=self.permit), patch.object(sender, 'finish_delivery_attempt'), patch.object(sender, 'is_push_configured', return_value=True), patch.object(sender, 'list_pending_push_notification_events', return_value=[event]), patch.object(sender, 'dispatch_transport', return_value={'disabled': True, 'not_started': True}), patch.object(sender, 'mark_push_notification_event_sent') as sent, patch.object(sender, 'mark_push_notification_event_failed') as failed, redirect_stdout(io.StringIO()):
            sender.main()
        sent.assert_not_called()
        failed.assert_not_called()

    def test_telegram_publisher_does_not_mark_disabled_send_as_published(self):
        import run_publish_notifications as sender
        post = {'id': 1, 'event_key': 'synthetic', 'rendered_text': 'test', 'event_type': 'synthetic'}
        output = io.StringIO()
        with patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': '1'}), patch.object(sender, 'get_connection', return_value=Mock()), patch.object(sender, 'claim_notification_delivery', return_value=self.permit), patch.object(sender, 'finish_delivery_attempt'), patch.object(sender, 'is_telegram_configured', return_value=True), patch.object(sender, 'list_pending_notification_posts', return_value=[post]), patch.object(sender, 'dispatch_transport', return_value={'disabled': True, 'not_started': True}), patch.object(sender, 'mark_notification_post_published') as published, patch.object(sender, 'mark_notification_post_failed') as failed, redirect_stdout(output):
            sender.main()
        published.assert_not_called()
        failed.assert_not_called()
        self.assertNotIn('published', output.getvalue())

    def test_enabled_publishers_record_simulated_success(self):
        import run_send_notifications as push
        import run_publish_notifications as telegram
        event = {'id': 1, 'event_key': 'synthetic', 'title': 'test', 'body': 'test', 'url': '/'}
        post = {'id': 1, 'event_key': 'synthetic', 'rendered_text': 'test', 'event_type': 'synthetic'}
        with patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': '1'}), patch.object(push, 'get_connection', return_value=Mock()), patch.object(push, 'claim_notification_delivery', return_value=self.permit), patch.object(push, 'finish_delivery_attempt'), patch.object(push, 'is_push_configured', return_value=True), patch.object(push, 'list_pending_push_notification_events', return_value=[event]), patch.object(push, 'dispatch_transport', return_value={'ok': True}) as send, patch.object(push, 'mark_push_notification_event_sent') as sent, redirect_stdout(io.StringIO()):
            push.main()
        send.assert_called_once()
        sent.assert_called_once()
        with patch.dict('os.environ', {'ENABLE_NOTIFICATIONS': '1'}), patch.object(telegram, 'get_connection', return_value=Mock()), patch.object(telegram, 'claim_notification_delivery', return_value=self.permit), patch.object(telegram, 'finish_delivery_attempt'), patch.object(telegram, 'is_telegram_configured', return_value=True), patch.object(telegram, 'list_pending_notification_posts', return_value=[post]), patch.object(telegram, 'dispatch_transport', return_value={'ok': True, 'result': {'message_id': 17}}) as send, patch.object(telegram, 'mark_notification_post_published') as published, redirect_stdout(io.StringIO()):
            telegram.main()
        send.assert_called_once()
        published.assert_called_once()
        self.assertEqual(published.call_args.kwargs['external_message_id'], '17')

    def test_detection_alert_does_not_claim_validated_processing(self):
        from app.storage.public_alert_store import render_public_alert_text
        text = render_public_alert_text(title='Synthetic', summary='Synthetic', url='/avisos')
        self.assertNotIn('Datos procesados', text)
        self.assertIn('detección', text)
