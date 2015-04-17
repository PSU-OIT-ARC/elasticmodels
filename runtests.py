import sys

import django
from django.conf import settings

settings.configure(
    DEBUG=True,
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
        }
    },
    INSTALLED_APPS=(
        'django.contrib.auth',
        'django.contrib.contenttypes',
        'django.contrib.sessions',
        'django.contrib.admin',
        'elasticmodels',
    ),
    MIDDLEWARE_CLASSES=[],
    ELASTICSEARCH_CONNECTIONS={
        'default': {
            'HOSTS': ['http://localhost:9200'],
            'INDEX_NAME': 'elasticmodels-unit-test-db',
        }
    },
    USE_TZ=True,
)

if django.VERSION[:2] >= (1, 7):
    from django import setup
else:
    setup = lambda: None

from django.test.runner import DiscoverRunner

setup()
test_runner = DiscoverRunner(verbosity=1)

failures = test_runner.run_tests(['elasticmodels', ])
if failures:
    sys.exit(failures)
