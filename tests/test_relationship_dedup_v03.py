import unittest

from adotracefoundry.core import extract_relations


class RelationshipDedupTests(unittest.TestCase):

    def test_forward_reverse_deduplication(self):
        def link(rel, target):
            return {
                "rel": rel,
                "url": (
                    "https://dev.azure.com/example/project/"
                    f"_apis/wit/workItems/{target}"
                ),
            }

        items = [
            {
                "id": 3,
                "relations": [
                    link("Microsoft.VSTS.Common.TestedBy-Forward", 391),
                    link("System.LinkTypes.Related", 6),
                ],
            },
            {
                "id": 391,
                "relations": [
                    link("Microsoft.VSTS.Common.TestedBy-Reverse", 3),
                ],
            },
            {
                "id": 6,
                "relations": [
                    link("System.LinkTypes.Related", 3),
                ],
            },
        ]

        edges, attachments = extract_relations(items)

        self.assertEqual(len(edges), 2)

        self.assertIn(
            {"kind": "tested-by", "source": 3, "target": 391},
            edges,
        )

        self.assertIn(
            {"kind": "related", "source": 3, "target": 6},
            edges,
        )

        self.assertEqual(attachments, [])


if __name__ == "__main__":
    unittest.main()
