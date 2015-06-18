import re
from unittest.mock import patch, Mock
from unittest import TestCase
from django.test.runner import DiscoverRunner

from .indexes import registry

def destroy():
    for index in registry.get_indexes():
        index.es.indices.delete(index=index._doc_type.index, ignore=[400, 404])


def create():
    for index in registry.get_indexes():
        index.put_mapping()

patches = [
    patch("elasticmodels.indexes.Index.bulk", Mock()),
    patch("elasticmodels.indexes.Index.put_mapping", Mock()),
    patch("elasticmodels.indexes.Index.delete_mapping", Mock()),
]

class SearchRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super(SearchRunner, self).setup_test_environment(**kwargs)
        for p in patches:
            p.start()

    def teardown_test_environment(self, **kwargs):
        for p in patches:
            p.stop()


class ESTestCase(TestCase):
    def setUp(self):
        for p in patches:
            p.stop()

        for index in registry.get_indexes():
            index._doc_type.index += "_test"

        destroy()
        create()

        super().setUp()

    def tearDown(self):
        destroy()

        for index in registry.get_indexes():
            re.sub("_test$", "", index._doc_type.index)

        for p in patches:
            p.start()

        super().tearDown()
