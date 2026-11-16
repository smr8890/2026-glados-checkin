import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import checkin


class FakeNotify:
    def __init__(self, channels=None, error=None):
        self.channels = [object()] if channels is None else channels
        self.error = error
        self.sent = []

    def add_notify_function(self):
        return self.channels

    def send(self, title, content):
        if self.error:
            raise self.error
        self.sent.append((title, content))


class QinglongNotifyTests(unittest.TestCase):
    def test_missing_module_is_skipped(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('checkin._load_qinglong_notify', return_value=None):
            self.assertIsNone(checkin.qinglong_notify('签到结果', '内容'))

    def test_disabled_by_environment_does_not_load_module(self):
        with mock.patch.dict(os.environ, {'QINGLONG_NOTIFY': '0'}, clear=True), \
                mock.patch('checkin._load_qinglong_notify') as load:
            self.assertIsNone(checkin.qinglong_notify('签到结果', '内容'))

        load.assert_not_called()

    def test_no_configured_channels_is_skipped(self):
        module = FakeNotify(channels=[])
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('checkin._load_qinglong_notify', return_value=module):
            self.assertIsNone(checkin.qinglong_notify('签到结果', '内容'))

        self.assertEqual(module.sent, [])

    def test_html_content_is_sent_as_plain_text(self):
        module = FakeNotify()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('checkin._load_qinglong_notify', return_value=module):
            self.assertTrue(checkin.qinglong_notify(
                '签到结果',
                '<p>成功</p><p>积分: <b>500</b></p>',
            ))

        self.assertEqual(module.sent, [('签到结果', '成功\n积分: 500')])

    def test_send_exception_is_contained(self):
        module = FakeNotify(error=RuntimeError('boom'))
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('checkin._load_qinglong_notify', return_value=module), \
                mock.patch('checkin.log') as log:
            self.assertFalse(checkin.qinglong_notify('签到结果', '内容'))

        log.assert_called_once()

    def test_loader_reads_sibling_notify_py(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'notify.py'
            path.write_text(
                'sent = []\n'
                'def add_notify_function():\n'
                '    return [object()]\n'
                'def send(title, content):\n'
                '    sent.append((title, content))\n',
                encoding='utf-8',
            )

            module = checkin._load_qinglong_notify(str(path))
            self.assertIsNotNone(module)
            module.send('标题', '正文')
            self.assertEqual(module.sent, [('标题', '正文')])


class NotificationDispatchTests(unittest.TestCase):
    def test_qinglong_only_counts_as_sent(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('checkin.qinglong_notify', return_value=True) as qinglong:
            sent_any, ok = checkin.send_notifications('标题', '正文')

        self.assertTrue(sent_any)
        self.assertTrue(ok)
        qinglong.assert_called_once_with('标题', '正文')

    def test_qinglong_failure_marks_dispatch_failed(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('checkin.qinglong_notify', return_value=False):
            sent_any, ok = checkin.send_notifications('标题', '正文')

        self.assertTrue(sent_any)
        self.assertFalse(ok)

    def test_no_channel_returns_not_sent(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('checkin.qinglong_notify', return_value=None):
            sent_any, ok = checkin.send_notifications('标题', '正文')

        self.assertFalse(sent_any)
        self.assertTrue(ok)

    def test_webhook_and_qinglong_both_receive_notification(self):
        with mock.patch.dict(os.environ, {'GLADOS_WEBHOOK_URL': 'https://example.test'}, clear=True), \
                mock.patch('checkin.webhook_push', return_value=True) as webhook, \
                mock.patch('checkin.qinglong_notify', return_value=True) as qinglong:
            sent_any, ok = checkin.send_notifications('标题', '正文')

        self.assertTrue(sent_any)
        self.assertTrue(ok)
        webhook.assert_called_once_with('标题', '正文')
        qinglong.assert_called_once_with('标题', '正文')


if __name__ == '__main__':
    unittest.main()
