#!/usr/bin/env python3
"""mailctl.call refuses a redirect, so the Bearer token never goes to another host.

    python3 -m unittest test_mailctl_redirect -v
"""
import unittest
import urllib.error
from unittest import mock

import mailctl
from test_wake_remote import Mailroom, RemoteBase, TOKEN


class CallRedirect(RemoteBase):
    def test_redirect_is_refused_and_the_token_does_not_follow(self):
        Mailroom.redirect = self.url.replace("127.0.0.1", "localhost") + "/elsewhere"
        with mock.patch.object(mailctl, "TOKEN", TOKEN):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                mailctl.call(self.url, "/inbox?node=remote1")
        self.assertEqual(cm.exception.code, 302)
        self.assertFalse([p for p, auth in Mailroom.seen if p.startswith("/elsewhere")])

    def test_a_plain_reply_still_works(self):
        with mock.patch.object(mailctl, "TOKEN", TOKEN):
            self.assertEqual(mailctl.call(self.url, "/inbox?node=remote1"), {"messages": []})


if __name__ == "__main__":
    unittest.main()
