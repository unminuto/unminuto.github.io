import unittest

from scripts.rss2broadcast import build_broadcast_payload, parse_feed, select_new_item


FEED = """<?xml version=\"1.0\"?>
<rss version=\"2.0\"><channel>
<title>Germania, in breve</title>
<item><title>Edizione del 28 settembre</title><link>https://example.com/28</link>
<guid>briefing-28</guid><pubDate>Mon, 28 Sep 2026 05:00:00 GMT</pubDate>
<description><![CDATA[<p>Il riepilogo del giorno.</p>]]></description></item>
</channel></rss>"""


class RSS2BroadcastTests(unittest.TestCase):
    def test_parse_feed_and_builds_kit_html_payload(self):
        item = parse_feed(FEED)[0]
        payload = build_broadcast_payload(item, send_at="2026-09-28T07:30:00+02:00")

        self.assertEqual(item.guid, "briefing-28")
        self.assertEqual(payload["subject"], "Edizione del 28 settembre")
        self.assertEqual(payload["send_at"], "2026-09-28T07:30:00+02:00")
        self.assertIn("Il riepilogo del giorno.", payload["content"])
        self.assertEqual(payload["preview_text"], "Il riepilogo del giorno.")
        self.assertIn("https://example.com/28", payload["content"])
        self.assertTrue(payload["public"])
        self.assertNotIn("subscriber_filter", payload)

    def test_select_new_item_ignores_processed_guid(self):
        items = parse_feed(FEED)
        self.assertIsNone(select_new_item(items, {"briefing-28"}))
        self.assertEqual(select_new_item(items, set()).guid, "briefing-28")

    def test_parse_atom_feed(self):
        atom = """<feed xmlns=\"http://www.w3.org/2005/Atom\">
        <entry><title>Atom edition</title><id>tag:example.com,2026:1</id>
        <link href=\"https://example.com/atom\"/><updated>2026-09-28T05:00:00Z</updated>
        <summary>Atom summary</summary></entry></feed>"""
        self.assertEqual(parse_feed(atom)[0].link, "https://example.com/atom")


if __name__ == "__main__":
    unittest.main()
