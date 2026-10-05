"""Embedding security-header decisions use synthetic headers and verified TLS."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock
from email.message import Message

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ran-selector'))
import embed_policy
import test_pending_adoption as adoption

class EmbedPolicyTests(unittest.TestCase):
    def test_headers_allow_unrestricted_and_reject_xfo(self):
        self.assertTrue(embed_policy.permits({},'https://app.invalid','http://dashboard.invalid'))
        for value in ('DENY','SAMEORIGIN','invalid'):
            self.assertFalse(embed_policy.permits({'X-Frame-Options':value},'https://app.invalid','http://dashboard.invalid'))
        self.assertTrue(embed_policy.permits({'X-Frame-Options':'SAMEORIGIN'},'https://app.invalid/path','https://app.invalid'))

    def test_csp_and_mixed_content(self):
        for policy in ("frame-ancestors 'none'", "frame-ancestors 'self'", 'frame-ancestors https://other.invalid'):
            self.assertFalse(embed_policy.permits({'Content-Security-Policy':policy},'https://app.invalid','https://dashboard.invalid'))
        self.assertTrue(embed_policy.permits({'Content-Security-Policy':'frame-ancestors https://dashboard.invalid'},'https://app.invalid','https://dashboard.invalid'))
        self.assertFalse(embed_policy.permits({},'http://app.invalid','https://dashboard.invalid'))
        headers=Message();headers.add_header('Content-Security-Policy','frame-ancestors *');headers.add_header('Content-Security-Policy',"frame-ancestors 'none'")
        self.assertFalse(embed_policy.permits(headers,'http://app.invalid','http://dashboard.invalid'))

    def test_read_only_fixed_url_tls_and_safe_failure(self):
        cfg={'OSM_HOST':'https://osm.invalid','OSM_CA_CERT_PATH':'synthetic-ca.crt'}
        with mock.patch.object(embed_policy.ssl,'create_default_context') as context, mock.patch.object(embed_policy,'urlopen') as opened:
            response=opened.return_value.__enter__.return_value;response.headers={'X-Frame-Options':'DENY'};response.geturl.return_value=cfg['OSM_HOST']
            self.assertEqual(embed_policy.inspect(cfg,'osm','http://dashboard.invalid'),{'allowed':False,'reason':'blocked'})
            context.assert_called_once_with(cafile='synthetic-ca.crt')
            args,kw=opened.call_args;self.assertEqual(args[0].full_url,cfg['OSM_HOST']);self.assertEqual(args[0].method,'GET');self.assertEqual(kw['timeout'],5)
            response.read.assert_not_called()
            opened.side_effect=OSError('private response detail')
            self.assertEqual(embed_policy.inspect(cfg,'osm','http://dashboard.invalid'),{'allowed':False,'reason':'unverified'})

    def test_unknown_application_rejected_without_requests(self):
        with mock.patch.object(adoption.backend.embed_policy,'inspect') as probe:
            response=adoption.backend.app.test_client().get('/api/embed-policy/arbitrary')
        self.assertEqual(response.status_code,400);probe.assert_not_called()
