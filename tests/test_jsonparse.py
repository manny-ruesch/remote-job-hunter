import unittest

from .helpers import ROOT  # noqa: F401  (configure sys.path)
from rjh.jsonparse import JsonParseError, parse_json_loose, salvage_items


class ParseLoose(unittest.TestCase):
    def test_fence_with_prose_around(self):
        doc = parse_json_loose('Voici le résultat :\n```json\n{"results":[{"id":"j1","score":8}]}\n```\nBonne chance !')
        self.assertEqual(doc["results"][0]["id"], "j1")

    def test_smart_quotes_from_a_chat_ui(self):
        doc = parse_json_loose('{“results”:[{“id”:“j1”,“score”:7.5}]}')
        self.assertEqual(doc["results"][0]["score"], 7.5)

    def test_trailing_commas(self):
        self.assertEqual(parse_json_loose('{"a":[1,2,],}'), {"a": [1, 2]})

    def test_unclosed_fence_but_complete_json(self):
        self.assertEqual(parse_json_loose('```json\n{"a":1}'), {"a": 1})

    def test_bare_json_without_fence(self):
        self.assertEqual(parse_json_loose('{"a":1}'), {"a": 1})

    def test_no_json_raises(self):
        with self.assertRaises(JsonParseError):
            parse_json_loose("Désolé, je ne peux pas répondre.")


class Salvage(unittest.TestCase):
    TRUNC = '```json\n{"results":[{"id":"j1","score":8,"why":"ok"},{"id":"j2","score":6,"why":"bon"},{"id":"j3","sc'

    def test_truncated_output_keeps_complete_items(self):
        s = salvage_items(self.TRUNC)
        self.assertEqual([i["id"] for i in s.items], ["j1", "j2"])
        self.assertTrue(s.truncated)

    def test_complete_output_is_not_flagged_truncated(self):
        s = salvage_items('{"results":[{"id":"j1"},{"id":"j2"}]}')
        self.assertEqual(len(s.items), 2)
        self.assertFalse(s.truncated)

    def test_nested_lists_of_objects_are_not_results(self):
        s = salvage_items('{"results":[{"id":"j1","flags":[{"a":1},{"b":2}],"keywords":["x"]}]}')
        self.assertEqual(len(s.items), 1)
        self.assertEqual(len(s.items[0]["flags"]), 2)

    def test_braces_and_quotes_inside_strings(self):
        s = salvage_items('{"results":[{"id":"j1","why":"a } b { c \\" d"},{"id":"j2","why":"x"},{"id":"j3","wh')
        self.assertEqual([i["id"] for i in s.items], ["j1", "j2"])

    def test_smart_quotes_and_truncation_together(self):
        s = salvage_items('{“results”:[{“id”:“j1”,“score”:8},{“id”:“j2”,“sco')
        self.assertEqual([i["id"] for i in s.items], ["j1"])
        self.assertTrue(s.truncated)

    def test_bare_array(self):
        self.assertEqual(len(salvage_items('[{"id":"j1"},{"id":"j2"}]').items), 2)

    def test_prose_with_braces_before_the_fence(self):
        s = salvage_items('Voici {le} résultat : ```json\n[{"id":"j1"}]\n```')
        self.assertEqual([i["id"] for i in s.items], ["j1"])

    def test_item_with_trailing_comma_is_repaired(self):
        self.assertEqual(len(salvage_items('{"results":[{"id":"j1","score":8,},{"id":"j2"}]}').items), 2)

    def test_garbage_gives_nothing(self):
        s = salvage_items("pas de json ici")
        self.assertEqual(s.items, [])


if __name__ == "__main__":
    unittest.main()
