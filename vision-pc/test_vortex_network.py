"""Deterministic SSRF checks; all DNS and socket connections are simulated."""
import os
import socket
import unittest
from unittest.mock import patch

import vortex_network as network


def answers(ip='93.184.216.34', port=443):
    family = socket.AF_INET6 if ':' in ip else socket.AF_INET
    target = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return [(family, socket.SOCK_STREAM, 6, '', target)]


class PublicMediaBoundaryTests(unittest.TestCase):
    def test_private_and_special_networks_are_never_public(self):
        blocked = ['127.0.0.1', '10.1.2.3', '172.16.8.1', '192.168.1.1',
                   '169.254.169.254', '100.64.0.1', '0.0.0.0', '224.0.0.1',
                   '255.255.255.255', '::1', '::', 'fe80::1', 'fc00::1',
                   'ff02::1', '::ffff:127.0.0.1', '::ffff:169.254.169.254']
        for value in blocked:
            with self.subTest(value=value):
                self.assertFalse(network.public_address(value))
        self.assertTrue(network.public_address('93.184.216.34'))
        self.assertTrue(network.public_address('2606:4700:4700::1111'))

    def test_credentials_controls_schemes_ports_and_local_hosts_rejected(self):
        bad = ['file:///etc/passwd', 'ftp://example.com/file', 'data:text/plain,x',
               'https://user:password@example.com/video', 'https://example.com:8765/video',
               'https://example.com:bad/video', 'https://127.0.0.1/', 'http://[::1]/',
               'http://[::ffff:127.0.0.1]/', 'http://169.254.169.254/latest/meta-data/',
               'https://localhost/', 'https://pc.local/', 'https://host.tailnet.ts.net/',
               'https://metadata.google.internal/', 'https://127.0.0.1%2f.example.com/',
               'https://example.com/\r\nX-Injected: yes', 'https://example.com/\x00',
               'https://example.com\\@127.0.0.1/', 'https:///missing-host']
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    network.validate_input(value, 'download', resolve=False)

    def test_dns_private_mixed_and_rebinding_answers_are_rejected(self):
        for records in ([], answers('127.0.0.1'), answers('169.254.169.254'),
                        answers() + answers('10.1.2.3'), answers('::ffff:127.0.0.1')):
            with self.subTest(records=records), patch.object(socket, 'getaddrinfo', return_value=records):
                with self.assertRaises(network.UnsafeDestination):
                    network.validate_input('https://media.example.com/video', 'download')
        with patch.object(socket, 'getaddrinfo', return_value=answers()):
            self.assertEqual(network.validate_input('https://media.example.com/video#ignored', 'download'),
                             'https://media.example.com/video')

    def test_search_and_source_routing_are_bounded(self):
        self.assertEqual(network.validate_input('my favorite song', 'inspect'), 'my favorite song')
        with self.assertRaises(ValueError):
            network.validate_input('my favorite song', 'download')
        for value in ('x' * 301, 'a\ncommand', ['query']):
            with self.assertRaises(ValueError):
                network.validate_input(value, 'inspect')
        for value, engine in [('https://youtube.com/watch?v=abc', 'yt-dlp'),
                              ('https://instagram.com/p/example', 'gallery-dl'),
                              ('https://open.spotify.com/track/example', 'spotdl'),
                              ('https://open.spotify.com.evil.example/video', 'yt-dlp')]:
            self.assertEqual(network.engine_for(value), engine)

    def test_worker_guard_checks_redirect_and_direct_socket_targets(self):
        connections = []

        def resolver(host, port, *unused):
            # A previously public hostname can now resolve to loopback. The
            # connection check must resolve and revalidate it independently.
            return answers('127.0.0.1' if host == 'rebound.example.com' else host
                           if host in ('127.0.0.1', '93.184.216.34') else '93.184.216.34', port)

        def connect(sock, target):
            connections.append(target)
            return 0

        self.assertFalse(getattr(socket, '_vortex_guard_installed', False))
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:8080'}), \
                patch.object(socket, 'getaddrinfo', side_effect=resolver), \
                patch.object(socket.socket, 'connect', connect), \
                patch.object(socket.socket, 'connect_ex', connect):
            try:
                network.install_network_guard()
                self.assertNotIn('HTTPS_PROXY', os.environ)
                with socket.socket() as sock:
                    for target in [('127.0.0.1', 443), ('rebound.example.com', 443),
                                   ('93.184.216.34', 8765)]:
                        with self.assertRaises(network.UnsafeDestination):
                            sock.connect(target)
                        with self.assertRaises(network.UnsafeDestination):
                            sock.connect_ex(target)
                    sock.connect(('public.example.com', 443))
                self.assertEqual(connections, [('93.184.216.34', 443)])
            finally:
                delattr(socket, '_vortex_guard_installed')


if __name__ == '__main__':
    unittest.main()
