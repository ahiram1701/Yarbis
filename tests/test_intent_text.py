import unittest

import intent_text


class IntentTextTestCase(unittest.TestCase):
    def test_normalize_intent_text_removes_accents_punctuation_and_case(self):
        self.assertEqual(
            intent_text.normalize_intent_text("Yarbis, reinicia la PC ahora!"),
            "yarbis reinicia la pc ahora",
        )

    def test_strip_yarbis_prefix_handles_common_prefixes(self):
        self.assertEqual(
            intent_text.strip_yarbis_prefix("Oye Yarbis, apaga la pc"),
            "apaga la pc",
        )

    def test_affirmative_action_reply_matches_shared_phrases(self):
        self.assertTrue(intent_text.looks_like_affirmative_action_reply("Si, hazlo"))
        self.assertTrue(intent_text.looks_like_affirmative_action_reply("adelante"))
        self.assertFalse(intent_text.looks_like_affirmative_action_reply("todavia no"))


if __name__ == "__main__":
    unittest.main()
